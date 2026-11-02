# app/services/platforms/wechat_service.py
"""微信公众号平台服务实现。

继承 PlatformService 基类，使用 PlatformAccount 中存储的凭证。

微信 HTTP 语义的**唯一实现**在 :mod:`app.services.wechat_core`；本模块是
「``platform_accounts`` 表凭据 → :class:`~app.services.wechat_core.WeChatApiClient`」
的薄适配器，与 ``.env`` 凭据路径（:mod:`app.services.wechat`）共用同一套
熔断器 / IP 白名单诊断 / 封面上传逻辑。
"""
import json
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.platforms.base import CrawledTopic, PlatformService, PublishResult
from app.services.wechat_core import WeChatApiClient

if TYPE_CHECKING:
    from app.models.platform import PlatformAccount

logger = logging.getLogger(__name__)


class WeChatPlatformService(PlatformService):
    """微信公众号平台服务"""

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)
        self._api_client: Optional[WeChatApiClient] = None

    # ------------------------------------------------------------------
    # 凭证
    # ------------------------------------------------------------------
    def _decrypt_credentials(self) -> Dict[str, Any]:
        """从 encrypted 凭证解密"""
        encrypted = self.account.credentials_encrypted
        if not encrypted:
            return {}

        try:
            fernet = Fernet(settings.effective_fernet_key)
            decrypted = fernet.decrypt(encrypted.encode()).decode()
            return json.loads(decrypted)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to decrypt credentials: %s", exc)
            return {}

    @property
    def api_client(self) -> WeChatApiClient:
        """底层微信客户端（延迟构建，缺凭证时也能构造出来便于报错）。"""
        if self._api_client is None:
            creds = self.credentials
            app_id = creds.get("app_id") or ""
            app_secret = creds.get("app_secret") or ""
            self._api_client = WeChatApiClient(
                app_id=app_id,
                app_secret=app_secret,
                api_url=creds.get("api_url") or settings.wechat_api_url,
                timeout=creds.get("timeout") or settings.wechat_timeout,
                # 账号级默认封面优先；没配则回退到全局配置。
                # 此前只认 credentials["default_cover_url"]，而管理后台表单
                # 并未提供该字段 → 走平台账号发布微信时必然拿不到封面而失败。
                default_cover_url=(
                    creds.get("default_cover_url")
                    or settings.wechat_default_cover_url
                ),
                # 素材 / token 按公众号（appid）隔离；极端情况下没有 appid
                # 时退化成账号维度，避免跨账号串号。
                scope=app_id or f"account-{self.account.account_id}",
            )
        return self._api_client

    # ------------------------------------------------------------------
    # PlatformService 接口
    # ------------------------------------------------------------------
    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取 access_token，优先走 Redis 缓存"""
        return self.api_client.get_access_token(force_refresh=force_refresh)

    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送客服消息"""
        return self.api_client.send_customer_message(openid, content)

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建草稿"""
        return self.api_client.create_draft(
            title,
            content,
            cover_url=cover_url,
            author=kwargs.get("author", "AI编辑部"),
            digest=kwargs.get("digest"),
        )

    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布文章（先创草稿，再发布）"""
        draft_result = self.create_draft(title, content, cover_url, **kwargs)
        if not draft_result:
            return PublishResult(success=False, errmsg="Failed to create draft")

        media_id = draft_result.get("media_id")
        if not media_id:
            return PublishResult(success=False, errmsg="No media_id in draft result")

        return self.publish_draft(media_id)

    def publish_draft(self, media_id: str) -> PublishResult:
        """发布草稿（返回统一 PublishResult）"""
        result = self.api_client.publish_draft(media_id)
        errcode = result.get("errcode", -1)
        if errcode == 0:
            return PublishResult(
                success=True,
                media_id=media_id,
                publish_id=result.get("publish_id", ""),
            )
        return PublishResult(
            success=False,
            errcode=errcode,
            errmsg=result.get("errmsg", ""),
        )

    def publish_draft_raw(self, media_id: str) -> Dict[str, Any]:
        """发布草稿，返回微信原始响应 dict（与 ``WeChatService`` 语义一致）。"""
        return self.api_client.publish_draft(media_id)

    def validate_credentials(self) -> bool:
        """凭据必须含 app_id / app_secret。"""
        creds = self.credentials
        return bool(creds.get("app_id")) and bool(creds.get("app_secret"))

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取微信热榜（暂用百度热搜作为来源）"""
        # 微信平台本身没有公开的热榜 API，这里复用百度热搜
        from app.services.crawler import BaiduHotCrawler
        crawler = BaiduHotCrawler()
        topics = crawler.crawl()
        return [
            CrawledTopic(
                title=t.get("word", ""),
                heat_score=float(t.get("hotScore", 0)),
                source_url=t.get("rawUrl", ""),
                summary=None,
                category="realtime",
                keywords=[],
                extra_metadata={"img_url": t.get("img", "")},
            )
            for t in topics
            if t.get("word")
        ]
