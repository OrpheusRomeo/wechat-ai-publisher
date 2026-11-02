# app/core/runtime_overrides.py
"""管理后台 ``system_settings`` → 运行时 ``settings`` 覆盖。

背景
----
管理后台「系统配置」页面写入的是 ``system_settings`` 表，但运行时**从不读回**：
``app/api/v1/settings.py`` 里的 ``_get_runtime_value()`` 定义了却没有任何调用点，
于是页面上改什么（每日发布配额、默认封面、客服消息开关、日报配色……）
都不生效 —— "修改后立即生效" 只是一句空话。

本模块负责把表里的值覆盖到 :data:`app.core.config.settings` 单例上：

- 进程启动时应用一次（FastAPI / Celery worker / Celery beat / Kafka consumer）
- 每个 Celery 任务 / 每条 Kafka 消息开始前应用一次（带 TTL 缓存，避免频繁查库）
- 管理后台保存配置后立即重新应用（``force=True``）

只覆盖**运行时确实会被读取**的键。凭证类配置（``app_id`` / ``app_secret`` /
``api_key``）不在此列：它们的权威来源是 ``platform_accounts`` / ``llm_providers``
表，对应的管理页面（平台账号管理 / LLM 供应商配置）才是正确入口。
"""
import logging
import time
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# 允许被 system_settings 覆盖的运行时配置 → 取值类型。
# 只列**运行时确实会被读到**的字段；不在表里的字段忽略。
_OVERRIDABLE: Dict[str, str] = {
    # === wechat ===
    # 凭据留空时不覆盖（种子数据就是空串），此时用 .env 的值
    "wechat_app_id": "str",
    "wechat_app_secret": "str",
    "wechat_default_cover_url": "str",
    "wechat_daily_publish_quota": "int",
    "wechat_enable_custom_message": "bool",
    "wechat_digest_color_title": "str",
    "wechat_digest_color_brief": "str",
    "wechat_digest_color_link": "str",
    # === scheduler ===
    "scheduler_crawl_time": "str",
    "scheduler_push_time": "str",
    "scheduler_topic_limit": "int",
    # === system ===
    "enable_auto_publish": "bool",
    "baidu_hot_use_mock": "bool",
}

# 进程内缓存：距上次应用不足 TTL 时直接跳过，避免每个任务都查库
_last_applied_at: Optional[float] = None
_CACHE_TTL_SECONDS = 15


def _cast_bool(raw: str) -> Optional[bool]:
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return None


def _cast_int(raw: str) -> Optional[int]:
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return None


def _cast(kind: str, raw: str) -> Optional[Any]:
    if kind == "bool":
        return _cast_bool(raw)
    if kind == "int":
        return _cast_int(raw)
    return raw


def _read_rows() -> list:
    """读取 system_settings 全部行（延迟导入，避免模块级循环依赖）。"""
    from app.core.database import session_scope
    from app.models.system_setting import SystemSetting

    with session_scope() as db:
        return db.query(SystemSetting).all()


def apply_runtime_overrides(
    force: bool = False,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> int:
    """把 ``system_settings`` 里的值覆盖到 ``settings`` 单例上。

    Args:
        force: 为 True 时忽略 TTL 缓存，强制重新读库并应用（管理后台保存后用）。
        ttl_seconds: 两次读库之间的最小间隔（秒）。

    Returns:
        实际应用的配置条数（缓存命中时返回 0）。
    """
    global _last_applied_at

    now = time.monotonic()
    if (
        not force
        and _last_applied_at is not None
        and (now - _last_applied_at) < ttl_seconds
    ):
        return 0
    _last_applied_at = now

    try:
        from app.core.security import decrypt_value

        rows = _read_rows()
    except Exception:  # noqa: BLE001
        # DB 不可用时不能让进程起不来（初始化阶段 DB 可能还没就绪）
        logger.warning("读取 system_settings 失败，跳过运行时覆盖", exc_info=True)
        return 0

    applied = 0
    for row in rows:
        kind = _OVERRIDABLE.get(row.key)
        if kind is None:
            continue

        # 注意字段命名：非敏感项明文存在 value_encrypted，敏感项存密文
        if row.is_secret:
            raw = decrypt_value(row.value_encrypted)
        else:
            raw = row.value_encrypted or ""
        raw = (raw or "").strip()
        if not raw:
            continue

        value = _cast(kind, raw)
        if value is None:
            logger.warning(
                "system_settings[%s] 取值非法, 已忽略: %r", row.key, raw,
            )
            continue

        setattr(settings, row.key, value)
        applied += 1

    if applied:
        logger.info("已从 system_settings 应用 %d 项运行时覆盖", applied)
    return applied


def reset_cache() -> None:
    """清空 TTL 缓存（测试用）。"""
    global _last_applied_at
    _last_applied_at = None
