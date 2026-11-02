# app/core/pii.py
"""PII (个人可识别信息) 日志脱敏工具 (#10).

需求背景:
- 微信公众号 openid 是准 PII, 日志聚合系统 (ELK/Loki) 会变成 PII 数据库
- 用户发送的消息内容 (text/event) 不应在 INFO 级别明文记录
- 内部排错时仍需能关联同一用户, 用散列做别名

实现:
- ``hash_openid``: 用 HMAC-SHA256(openid, salt) 取前 12 字符 hex
  - salt 来自 ``settings.pii_log_salt``, 通过环境变量配置
  - 不同部署用不同 salt, 跨部署无法反向关联同一用户
- ``truncate_content``: 保留前 max_chars 字符, 超过部分用省略号

修复 L3: salt 默认值的 warning 只在第一次调用时输出一次, 不是每次调用都打.
"""
import hashlib
import hmac
import logging
import threading
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SALT = "change-me-pii-salt"

# 修复 L3: 用 _lock 守护一次性的默认值警告
_salt_warn_lock = threading.Lock()
_salt_warned = False


def _salt() -> bytes:
    raw = getattr(settings, "pii_log_salt", None) or DEFAULT_SALT
    if raw == DEFAULT_SALT:
        global _salt_warned
        with _salt_warn_lock:
            if not _salt_warned:
                logger.warning(
                    "PII_LOG_SALT 未配置, 使用默认值, 跨部署不可关联"
                )
                _salt_warned = True
    return raw.encode("utf-8")


def hash_openid(openid: Optional[str]) -> str:
    """返回 openid 的不可逆散列 (12 字符 hex).

    空值返回 ``"<empty>"`` 而不是空串, 避免日志格式对齐问题.
    """
    if not openid:
        return "<empty>"
    digest = hmac.new(_salt(), openid.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:12]


def truncate_content(content: Optional[str], max_chars: int = 50) -> str:
    """截断消息内容, 仅保留前 max_chars 字符 + 长度信息."""
    if not content:
        return "<empty>"
    if len(content) <= max_chars:
        return content
    return f"{content[:max_chars]}... (+{len(content) - max_chars} chars)"


def mask_openid(openid: Optional[str], keep_prefix: int = 4, keep_suffix: int = 2) -> str:
    """另一种脱敏方式: 仅保留前后几位, 中间用 **** 替换.

    用于必须保留可读性的场景, 例如报警信息中能看出"哪个公众号账号"
    但不暴露完整 openid.
    """
    if not openid:
        return "<empty>"
    if len(openid) <= keep_prefix + keep_suffix:
        return "*" * len(openid)
    return f"{openid[:keep_prefix]}****{openid[-keep_suffix:]}"