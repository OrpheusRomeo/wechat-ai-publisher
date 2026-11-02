# app/services/platforms/crawlers.py
"""各平台爬虫实现

每个平台都有自己的热榜/内容来源，实现统一的爬虫接口。
这些爬虫与平台服务（PlatformService）配合使用。
"""
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any

import requests

from app.services.platforms.base import CrawledTopic

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """爬虫基类"""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        self.credentials = credentials or {}

    @abstractmethod
    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取热榜话题"""
        pass

    def _get_session(self) -> requests.Session:
        """获取带默认头的 session"""
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        return session


class DouyinHotCrawler(BaseCrawler):
    """抖音热榜爬虫

    使用抖音开放平台热榜 API
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取抖音热榜"""
        from app.core.redis_client import redis_client

        client_key = self.credentials.get("client_key")
        client_secret = self.credentials.get("client_secret")

        if not client_key or not client_secret:
            logger.warning("Douyin credentials not configured")
            return []

        cache_key = f"douyin:crawler_token:{client_key}"
        token = redis_client.run_sync(redis_client.get, cache_key)

        if not token:
            try:
                # 获取 access token
                token_url = "https://open.douyin.com/oauth/client_token/"
                params = {
                    "client_key": client_key,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                }
                response = requests.get(token_url, params=params, timeout=30)
                result = response.json()

                if result.get("data", {}).get("access_token"):
                    token = result["data"]["access_token"]
                    expires_in = result["data"].get("expires_in", 7200) - 300
                    redis_client.run_sync(redis_client.set, cache_key, token, expires_in)
                else:
                    return []
            except Exception as e:
                logger.error("Failed to get Douyin token: %s", e)
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

        except Exception as e:
            logger.error("Douyin crawl error: %s", e)

        return []


class XiaohongshuCrawler(BaseCrawler):
    """小红书热榜爬虫

    通过非官方方式获取热榜内容（需登录态）
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取小红书热榜"""
        xhs_token = self.credentials.get("xhs_token")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.xiaohongshu.com/",
        }

        if xhs_token:
            headers["Cookie"] = f"xhs_token={xhs_token}"

        try:
            # 尝试小红书探索页热榜
            url = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"
            params = {
                "keyword": "热搜",
                "page": 1,
                "page_size": limit,
                "search_id": "",
                "sort": "general",
                "note_type": 0,
            }

            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return self._parse_response(data, limit)

        except Exception as e:
            logger.debug(f"Xiaohongshu API error: {e}")

        # 如果 API 不可用，返回空
        logger.warning("Xiaohongshu hot search not available")
        return []

    def _parse_response(self, data: dict, limit: int) -> List[CrawledTopic]:
        """解析小红书响应"""
        topics = []
        try:
            items = data.get("data", {}).get("items", [])
            for item in items[:limit]:
                if isinstance(item, dict):
                    topics.append(CrawledTopic(
                        title=item.get("title", ""),
                        heat_score=float(item.get("like_count", 0) or 0),
                        source_url=item.get("share_url", ""),
                        summary=item.get("desc", "")[:200] if item.get("desc") else None,
                        category=item.get("tag", ""),
                        keywords=[],
                        extra_metadata={
                            "note_id": item.get("note_id", ""),
                        },
                    ))
        except Exception as e:
            logger.error(f"Failed to parse Xiaohongshu response: {e}")
        return topics


class BaijiahaoCrawler(BaseCrawler):
    """百度百家号热榜爬虫

    百家号是百度旗下的内容平台，有公开的热榜接口
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取百家号热榜"""
        session = self._get_session()

        try:
            # 百家号热榜
            url = "https://baijiahao.baidu.com/builderinner/api/resource/builderfeed/getBuilderFeedList"
            params = {
                "num": limit,
                "page": 1,
                "tag": "",
                "type": "hot",
            }

            response = session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return self._parse_response(data, limit)

        except Exception as e:
            logger.error("Baijiahao crawl error: %s", e)

        return []

    def _parse_response(self, data: dict, limit: int) -> List[CrawledTopic]:
        """解析百家号响应"""
        topics = []
        try:
            items = data.get("data", {}).get("list", [])
            for item in items[:limit]:
                if isinstance(item, dict):
                    topics.append(CrawledTopic(
                        title=item.get("title", ""),
                        heat_score=float(item.get("hot_score", 0) or 0),
                        source_url=item.get("url", ""),
                        summary=item.get("digest", "")[:200] if item.get("digest") else None,
                        category=item.get("tag", ""),
                        keywords=[],
                        extra_metadata={
                            "author": item.get("author", ""),
                        },
                    ))
        except Exception as e:
            logger.error(f"Failed to parse Baijiahao response: {e}")
        return topics


class ToutiaoCrawler(BaseCrawler):
    """头条热榜爬虫

    使用头条热榜 API
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取头条热榜"""
        from app.core.redis_client import redis_client

        client_key = self.credentials.get("client_key")
        client_secret = self.credentials.get("client_secret")

        if not client_key or not client_secret:
            logger.warning("Toutiao credentials not configured")
            return []

        cache_key = f"toutiao:crawler_token:{client_key}"
        token = redis_client.run_sync(redis_client.get, cache_key)

        if not token:
            try:
                token_url = "https://open.toutiao.com/oauth/client_token/"
                params = {
                    "client_key": client_key,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                }
                response = requests.get(token_url, params=params, timeout=30)
                result = response.json()

                if result.get("data", {}).get("access_token"):
                    token = result["data"]["access_token"]
                    expires_in = result["data"].get("expires_in", 7200) - 300
                    redis_client.run_sync(redis_client.set, cache_key, token, expires_in)
                else:
                    return []
            except Exception as e:
                logger.error("Failed to get Toutiao token: %s", e)
                return []

        try:
            url = "https://open.toutiao.com/hot_search/hot_word/list/"
            headers = {"Access-Token": token}
            response = requests.get(url, headers=headers, timeout=30)
            result = response.json()

            if result.get("err_no") == 0:
                topics = []
                for item in result.get("data", [])[:limit]:
                    topics.append(CrawledTopic(
                        title=item.get("word", ""),
                        heat_score=float(item.get("hot_value", 0) or 0),
                        source_url=item.get("link", ""),
                        summary=None,
                        category="hot_search",
                        keywords=[],
                        extra_metadata={
                            "hot_value": item.get("hot_value", ""),
                        },
                    ))
                return topics

        except Exception as e:
            logger.error("Toutiao crawl error: %s", e)

        return []


class YouTubeTrendingCrawler(BaseCrawler):
    """YouTube 热门视频爬虫

    使用 YouTube Data API v3 的 trending 端点
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取 YouTube 热门视频"""
        api_key = self.credentials.get("api_key")
        if not api_key:
            logger.warning("YouTube API key not configured")
            return []

        try:
            url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "regionCode": "CN",
                "maxResults": min(limit, 50),
                "key": api_key,
            }
            response = requests.get(url, params=params, timeout=30)
            result = response.json()

            topics = []
            for item in result.get("items", []):
                snippet = item.get("snippet", {})
                topics.append(CrawledTopic(
                    title=snippet.get("title", ""),
                    heat_score=float(item.get("statistics", {}).get("viewCount", 0)),
                    source_url=f"https://youtube.com/watch?v={item['id']}",
                    summary=snippet.get("description", "")[:200],
                    category=snippet.get("categoryId", ""),
                    keywords=[],
                    extra_metadata={
                        "channel_title": snippet.get("channelTitle", ""),
                        "video_id": item["id"],
                    },
                ))
            return topics
        except Exception as exc:
            logger.error("YouTube crawler error: %s", exc)
            return []


class WeChatHotCrawler(BaseCrawler):
    """微信公众号热榜（使用百度热搜作为来源）

    微信公众号本身没有公开的热榜 API，
    暂用百度热搜作为内容来源。
    """

    def crawl(self, limit: int = 20) -> List[CrawledTopic]:
        """爬取微信热榜（复用百度热搜）"""
        from app.services.crawler import BaiduHotCrawler
        crawler = BaiduHotCrawler()
        raw_topics = crawler.crawl()

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
            for t in raw_topics[:limit]
            if t.get("word")
        ]


# 爬虫工厂
class CrawlerFactory:
    """爬虫工厂"""

    _crawlers = {
        "wechat": WeChatHotCrawler,
        "douyin": DouyinHotCrawler,
        "xiaohongshu": XiaohongshuCrawler,
        "baijiahao": BaijiahaoCrawler,
        "toutiao": ToutiaoCrawler,
        "youtube": YouTubeTrendingCrawler,
    }

    @classmethod
    def get_crawler(cls, platform_type: str, credentials: Optional[Dict] = None) -> BaseCrawler:
        """获取爬虫实例"""
        crawler_class = cls._crawlers.get(platform_type)
        if not crawler_class:
            raise ValueError(f"Unsupported platform: {platform_type}")
        return crawler_class(credentials)

    @classmethod
    def register(cls, platform_type: str, crawler_class: type) -> None:
        """注册爬虫"""
        cls._crawlers[platform_type] = crawler_class

    @classmethod
    def get_supported_platforms(cls) -> List[str]:
        """获取支持的平台列表"""
        return list(cls._crawlers.keys())
