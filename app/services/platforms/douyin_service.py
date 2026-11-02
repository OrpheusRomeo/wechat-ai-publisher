# app/services/platforms/douyin_service.py
"""抖音平台服务实现。

抖音开放平台提供了创作者 API，可以获取热榜和发布内容。
文档: https://open.douyin.com/
"""
import json
import logging
from typing import Dict, Optional, List, Any

import requests

from app.services.platforms.base import PlatformService, PublishResult, CrawledTopic

logger = logging.getLogger(__name__)


class DouyinPlatformService(PlatformService):
    """抖音平台服务

    使用抖音开放平台 API:
    - 热榜: https://open.douyin.com/docs/online_ instructions/603_1_487_577
    - 发布: https://open.douyin.com/docs/online_ instructions/603_1_487_575
    """

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)
        self._client_key = None
        self._client_secret = None
        self._access_token = None

    def _decrypt_credentials(self) -> Dict[str, Any]:
        """解密凭证"""
        from cryptography.fernet import Fernet
        from app.core.config import settings

        encrypted = self.account.credentials_encrypted
        if not encrypted:
            return {}

        try:
            fernet = Fernet(settings.effective_fernet_key)
            decrypted = fernet.decrypt(encrypted.encode()).decode()
            return json.loads(decrypted)
        except Exception as exc:
            logger.error("Failed to decrypt Douyin credentials: %s", exc)
            return {}

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取抖音 API 访问令牌

        使用 client_credentials 模式
        """
        from app.core.redis_client import redis_client

        cache_key = f"douyin:access_token:{self.account.account_id}"

        if not force_refresh:
            cached = redis_client.run_sync(redis_client.get, cache_key)
            if cached:
                return cached if isinstance(cached, str) else None

        self._client_key = self.credentials.get("client_key")
        self._client_secret = self.credentials.get("client_secret")

        if not self._client_key or not self._client_secret:
            logger.error("Douyin client_key or client_secret not configured")
            return None

        try:
            url = "https://open.douyin.com/oauth/client_token"
            params = {
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            }
            response = requests.get(url, params=params, timeout=30)
            result = response.json()

            if "data" in result and "access_token" in result["data"]:
                token = result["data"]["access_token"]
                expires_in = result["data"].get("expires_in", 7200) - 300
                redis_client.run_sync(redis_client.set, cache_key, token, expires_in)
                self._access_token = token
                return token

            logger.error("Failed to get Douyin access token: %s", result)
            return None

        except Exception as exc:
            logger.error("Douyin access token error: %s", exc)
            return None

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取抖音热榜

        使用抖音创作者平台热榜接口
        """
        limit = kwargs.get("limit", 20)
        token = self.get_access_token()

        if not token:
            logger.warning("Douyin access token not available")
            return []

        try:
            # 抖音热榜接口
            url = "https://open.douyin.com/douyin/v1/hot_search/bolton/"
            headers = {"Access-Token": token}

            response = requests.get(url, headers=headers, timeout=30)
            result = response.json()

            if result.get("err_no") == 0:
                topics = []
                for item in result.get("bolton_info", [])[:limit]:
                    topics.append(CrawledTopic(
                        title=item.get("word", ""),
                        heat_score=float(item.get("hot_value", 0) or 0),
                        source_url=item.get("link", ""),
                        summary=None,
                        category="hot_search",
                        keywords=[],
                        extra_metadata={
                            "word_cover": item.get("word_cover", ""),
                            "hot_value": item.get("hot_value", ""),
                        },
                    ))
                return topics
            else:
                logger.error("Douyin hot search API error: %s", result)
                return []

        except Exception as exc:
            logger.error("Douyin crawl error: %s", exc)
            return []

    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布抖音视频/图文

        抖音主要支持视频内容，图文是辅助。
        这里实现视频发布接口。
        """
        token = self.get_access_token()
        if not token:
            return PublishResult(success=False, errmsg="Not authenticated")

        api_url = "https://open.douyin.com/douyin/v1/video/upload/"

        try:
            # 构建视频数据
            # 注意：抖音主要发布视频，这里需要 video_data
            video_data = kwargs.get("video_data")
            if not video_data:
                return PublishResult(
                    success=False,
                    errmsg="Douyin requires video content, not text articles"
                )

            files = {"video": video_data}
            data = {
                "title": title[:100],  # 标题限制
                "description": content[:500],  # 描述限制
                "topics": ",".join(kwargs.get("tags", [])),
            }
            headers = {"Access-Token": token}

            response = requests.post(api_url, files=files, data=data, headers=headers, timeout=60)
            result = response.json()

            if result.get("err_no") == 0:
                return PublishResult(
                    success=True,
                    media_id=result.get("video_id", ""),
                    url=result.get("share_url", ""),
                )
            else:
                return PublishResult(
                    success=False,
                    errcode=result.get("err_no"),
                    errmsg=result.get("err_tips", ""),
                )

        except Exception as exc:
            logger.error("Douyin publish error: %s", exc)
            return PublishResult(success=False, errmsg=str(exc))

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建草稿"""
        # 抖音不支持草稿直接创建，返回 None
        return None

    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送客服消息

        抖音支持通过客服消息接口发送消息
        """
        token = self.get_access_token()
        if not token:
            return False

        try:
            url = "https://open.douyin.com/douyin/v1/message/custom/send/"
            headers = {"Access-Token": token}
            payload = {
                "open_id": openid,
                "msg_type": "text",
                "content": content,
            }

            response = requests.post(url, json=payload, headers=headers, timeout=30)
            result = response.json()
            return result.get("err_no") == 0

        except Exception as exc:
            logger.error("Douyin send message error: %s", exc)
            return False
