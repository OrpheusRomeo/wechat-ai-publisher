# app/services/wechat.py
"""微信公众平台 API 调用（``.env`` 凭据入口）。

本项目运行在 **订阅号**（非服务号）上, 因此只暴露订阅号可用的接口:

- ``send_custom_message`` : 客服消息 (用户需在 48h 互动窗口内)
- ``create_draft`` : 草稿箱 (不受群发配额限制)
- ``publish_draft`` : 群发 (受每日配额限制, 见 ``WECHAT_DAILY_PUBLISH_QUOTA``)

所有方法都是 **同步** 形式。Celery worker / Kafka consumer / FastAPI 路径
都能直接调用。原 ``async def`` + 内部用 ``requests`` 同步阻塞的假异步模式
已废弃, 参见 CHANGELOG / commit 历史。

微信 HTTP 语义的**唯一实现**在 :mod:`app.services.wechat_core`；本模块只是
「``.env`` 凭据 → :class:`~app.services.wechat_core.WeChatApiClient`」的薄适配器。
另一份入口（``platform_accounts`` 表凭据）在
:mod:`app.services.platforms.wechat_service`，同样只是薄适配器。
"""
import logging
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.wechat_core import (
    QUOTA_ERRCODES,
    WeChatApiClient,
    is_ip_whitelist_error,
    log_ip_whitelist_error,
)

logger = logging.getLogger(__name__)

# 兼容旧引用名（app.tasks.article_tasks 依赖 ``_QUOTA_ERRCODES``）
_QUOTA_ERRCODES = QUOTA_ERRCODES

# 兼容旧引用名
_is_ip_whitelist_error = is_ip_whitelist_error
_log_ip_whitelist_error = log_ip_whitelist_error


class WeChatService:
    """基于 ``.env`` 凭据的微信客户端。

    只做两件事：把 ``.env`` 的凭据转成 :class:`WeChatApiClient`，以及把
    核心层 dict 语义适配成历史 API（``send_custom_message`` /
    ``publish_draft`` 返回 dict）。
    """

    def __init__(self, client: Optional[WeChatApiClient] = None) -> None:
        self._client = client or WeChatApiClient(
            app_id=settings.wechat_app_id,
            app_secret=settings.wechat_app_secret,
            api_url=settings.wechat_api_url,
            timeout=settings.wechat_timeout,
            default_cover_url=settings.wechat_default_cover_url,
            scope=settings.wechat_app_id,
        )

    # ------------------------------------------------------------------
    # 只读属性（历史 API 兼容）
    # ------------------------------------------------------------------
    @property
    def app_id(self) -> str:
        return self._client.app_id

    @property
    def app_secret(self) -> str:
        return self._client.app_secret

    @property
    def api_url(self) -> str:
        return self._client.api_url

    @property
    def timeout(self) -> int:
        return self._client.timeout

    @property
    def api_client(self) -> WeChatApiClient:
        """底层客户端（需要与平台账号路径共享同一套实现时使用）。"""
        return self._client

    # ------------------------------------------------------------------
    # 微信 API
    # ------------------------------------------------------------------
    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取 ``access_token``，优先走 Redis 缓存。"""
        return self._client.get_access_token(force_refresh=force_refresh)

    def send_custom_message(self, openid: str, content: str) -> bool:
        """发送客服消息（48h 互动窗口内才有效）。"""
        return self._client.send_customer_message(openid, content)

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        author: str = "AI编辑部",
        digest: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建草稿，返回 ``{"media_id": ...}``；失败返回 None。"""
        return self._client.create_draft(
            title, content, cover_url=cover_url, author=author, digest=digest,
        )

    def publish_draft(self, media_id: str) -> Dict[str, Any]:
        """群发草稿，返回微信原始响应 dict（含 ``errcode`` / ``publish_id``）。"""
        return self._client.publish_draft(media_id)

    # ------------------------------------------------------------------
    # 工具方法（历史 API 兼容，测试直接调用）
    # ------------------------------------------------------------------
    @staticmethod
    def _markdown_to_html(content: str) -> str:
        """Markdown → HTML 转换（使用共享模块）。"""
        return WeChatApiClient._markdown_to_html(content)

    @staticmethod
    def _truncate_to_chars(text: str, max_chars: int) -> str:
        """按字符数截断, 用于微信 title 字段 (硬限制 17 字符)."""
        return WeChatApiClient._truncate_to_chars(text, max_chars)

    @staticmethod
    def _truncate_to_bytes(text: str, max_bytes: int) -> str:
        """按 UTF-8 字节数截断, 用于微信 author 字段 (硬限制 16 字节)."""
        return WeChatApiClient._truncate_to_bytes(text, max_bytes)
