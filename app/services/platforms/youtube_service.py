# app/services/platforms/youtube_service.py
"""YouTube 平台服务实现。

使用 YouTube Data API v3 进行热榜获取和视频发布。
文档: https://developers.google.com/youtube/v3/
"""
import json
import logging
from typing import Dict, Optional, List, Any

import requests

from app.services.platforms.base import PlatformService, PublishResult, CrawledTopic

logger = logging.getLogger(__name__)


class YouTubePlatformService(PlatformService):
    """YouTube 平台服务

    使用 Google YouTube Data API v3:
    - 热榜: videos().list(part="snippet,statistics", chart="mostPopular")
    - 发布: videos().insert() - 需要 OAuth
    """

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)

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
            logger.error("Failed to decrypt YouTube credentials: %s", exc)
            return {}

    def get_access_token(self) -> Optional[str]:
        """获取访问令牌

        YouTube API key 方式不需要 access_token，直接用 API key
        """
        api_key = self.credentials.get("api_key")
        return api_key if api_key else None

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取 YouTube 热门视频

        使用 YouTube Data API v3 的 mostPopular 图表
        """
        api_key = self.credentials.get("api_key")
        channel_id = self.credentials.get("channel_id")
        limit = kwargs.get("limit", 20)
        region_code = kwargs.get("region_code", "CN")

        if not api_key:
            logger.warning("YouTube API key not configured")
            return []

        try:
            url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": region_code,
                "maxResults": min(limit, 50),
                "key": api_key,
            }

            response = requests.get(url, params=params, timeout=30)
            result = response.json()

            topics = []
            for item in result.get("items", []):
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                content_details = item.get("contentDetails", {})

                topics.append(CrawledTopic(
                    title=snippet.get("title", ""),
                    heat_score=float(statistics.get("viewCount", 0) or 0),
                    source_url=f"https://youtube.com/watch?v={item['id']}",
                    summary=snippet.get("description", "")[:300],
                    category=snippet.get("categoryId", ""),
                    keywords=snippet.get("tags", []),
                    extra_metadata={
                        "channel_title": snippet.get("channelTitle", ""),
                        "channel_id": snippet.get("channelId", ""),
                        "video_id": item["id"],
                        "view_count": statistics.get("viewCount", 0),
                        "like_count": statistics.get("likeCount", 0),
                        "comment_count": statistics.get("commentCount", 0),
                        "duration": content_details.get("duration", ""),
                        "published_at": snippet.get("publishedAt", ""),
                    },
                ))

            return topics

        except Exception as exc:
            logger.error("YouTube crawl error: %s", exc)
            return []

    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布 YouTube 视频

        注意：YouTube 主要发布视频内容，文章发布需要通过博客方式。
        这里预留接口，实际使用需要 OAuth 认证和视频文件。
        """
        video_data = kwargs.get("video_data")
        if not video_data:
            return PublishResult(
                success=False,
                errmsg="YouTube requires video content. No video_data provided."
            )

        api_key = self.credentials.get("api_key")
        if not api_key:
            return PublishResult(success=False, errmsg="YouTube API key not configured")

        # YouTube 视频发布需要 OAuth，这里返回错误提示
        return PublishResult(
            success=False,
            errmsg="YouTube video upload requires OAuth 2.0 authentication. "
                   "Please implement OAuth flow at https://console.cloud.google.com/"
        )

    def create_draft(self, title: str, content: str, cover_url: Optional[str] = None, **kwargs) -> Optional[Dict[str, Any]]:
        """创建草稿 - YouTube 不支持文章草稿"""
        return None

    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送消息 - YouTube 不支持主动消息"""
        logger.warning("YouTube does not support proactive customer messages")
        return False
