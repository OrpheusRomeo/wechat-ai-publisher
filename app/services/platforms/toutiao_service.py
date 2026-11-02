# app/services/platforms/toutiao_service.py
"""头条平台服务实现。

头条开放平台 API 文档: https://open.toutiao.com/

认证方式: client_credentials (无用户授权)
IP 白名单: 必须在字节跳动开放平台后台添加服务器出口 IP
"""
import json
import logging
from typing import Dict, Optional, List, Any

import requests

from app.core.redis_client import redis_client
from app.services.platforms.base import PlatformService, PublishResult, CrawledTopic

logger = logging.getLogger(__name__)

# 头条 API 错误码（常见）
_TOKEN_ERRCODES = {10003, 10009, 10101}
_IP_WHITELIST_ERRCODES = {10103}
# 凭证无效错误
_INVALID_CREDENTIALS_ERRNOS = {10001, 10002, 10010, 10019}


def _is_ip_whitelist_error(err_no: int) -> bool:
    return err_no in _IP_WHITELIST_ERRCODES


def _is_invalid_credentials_error(err_no: int) -> bool:
    return err_no in _INVALID_CREDENTIALS_ERRNOS


def _log_ip_error(api_name: str, result: dict) -> None:
    logger.error(
        "头条 API [%s] 触发 IP 白名单限制 (err_no=%s): %s\n"
        "请登录字节跳动开放平台 → 应用管理 → 安全域名/IP白名单，添加本服务器出口 IP。",
        api_name, result.get("err_no"), result.get("err_msg", ""),
    )


class ToutiaoPlatformService(PlatformService):
    """头条平台服务

    使用字节跳动开放平台 API:
    - 热榜: https://open.toutiao.com/docs/api/270
    - 发布: https://open.toutiao.com/docs/api/144
    """

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)
        self._cache_key = f"toutiao:access_token:{account.account_id}"
        self._timeout = 30

    def _decrypt_credentials(self) -> Dict[str, Any]:
        """从 encrypted 凭证解密"""
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
            logger.error("Failed to decrypt Toutiao credentials: %s", exc)
            return {}

    # -------------------------------------------------------------------------
    # 认证
    # -------------------------------------------------------------------------

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取头条 API 访问令牌，优先走 Redis 缓存"""
        if not force_refresh:
            cached = redis_client.run_sync(redis_client.get, self._cache_key)
            if cached:
                ttl = redis_client.run_sync(redis_client.ttl, self._cache_key)
                if ttl and ttl > 60:
                    return cached if isinstance(cached, str) else None
                logger.info("头条 access_token 临近过期 (ttl=%s), 主动刷新", ttl)

        client_key = self.credentials.get("client_key")
        client_secret = self.credentials.get("client_secret")
        if not client_key or not client_secret:
            logger.error("Toutiao client_key or client_secret not configured for account_id=%s",
                         self.account.account_id)
            return None

        try:
            url = "https://open.toutiao.com/oauth/client_token/"
            params = {
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            }
            response = requests.get(url, params=params, timeout=self._timeout)
            result = response.json()

            if result.get("err_no") == 0 and "data" in result:
                token = result["data"].get("access_token")
                expires_in = result["data"].get("expires_in", 7200) - 300
                if token:
                    redis_client.run_sync(redis_client.set, self._cache_key, token, expires_in)
                    logger.info("头条 access_token 获取成功，expires_in=%ds", expires_in)
                    return token

            logger.error("获取头条 access_token 失败: %s", result)
            return None

        except requests.RequestException as exc:
            logger.error("获取头条 access_token 网络异常: %s", exc)
            return None

    def validate_credentials(self) -> Dict[str, Any]:
        """验证 client_key / client_secret 是否有效。

        通过申请 access_token 判断凭证是否正确。
        - 有效：返回 {"valid": True}
        - 无效：返回 {"valid": False, "reason": "client_key 或 client_secret 错误，请检查平台账号凭证"}
        - IP 白名单：返回 {"valid": False, "reason": "IP 不在白名单中，请添加服务器出口 IP 到字节跳动开放平台"}
        - 网络错误：返回 {"valid": False, "reason": "网络异常，无法验证凭证"}
        """
        client_key = self.credentials.get("client_key")
        client_secret = self.credentials.get("client_secret")
        if not client_key or not client_secret:
            return {"valid": False, "reason": "client_key 或 client_secret 未配置，请检查平台账号凭证"}

        try:
            url = "https://open.toutiao.com/oauth/client_token/"
            params = {
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            }
            response = requests.get(url, params=params, timeout=self._timeout)
            result = response.json()

            err_no = result.get("err_no", -1)
            if err_no == 0 and "data" in result:
                return {"valid": True}

            # IP 白名单
            if _is_ip_whitelist_error(err_no):
                return {
                    "valid": False,
                    "reason": (
                        "服务器 IP 不在白名单中。"
                        "请在「平台 IP 白名单」页面检测本机 IP，"
                        "然后添加到字节跳动开放平台 → 应用管理 → 安全设置 → IP 白名单。"
                    ),
                }

            # 凭证错误
            if _is_invalid_credentials_error(err_no):
                return {
                    "valid": False,
                    "reason": "client_key 或 client_secret 错误，请检查平台账号凭证",
                }

            return {
                "valid": False,
                "reason": f"验证失败: err_no={err_no} err_msg={result.get('err_msg', '')}",
            }

        except requests.RequestException as exc:
            logger.warning("验证头条凭证网络异常: %s", exc)
            return {"valid": False, "reason": "网络异常，无法验证凭证"}

    # -------------------------------------------------------------------------
    # 热榜
    # -------------------------------------------------------------------------

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取头条热榜

        使用头条热榜接口: https://open.toutiao.com/docs/api/270
        """
        limit = kwargs.get("limit", 20)
        token = self.get_access_token()
        if not token:
            logger.warning("Toutiao access token not available, skip crawl")
            return []

        try:
            url = "https://open.toutiao.com/hot/search/hot_word/list/"
            headers = {"Access-Token": token}
            response = requests.get(url, headers=headers, timeout=self._timeout)
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
                            "word_cover": item.get("word_cover", ""),
                            "hot_value": item.get("hot_value", ""),
                        },
                    ))
                logger.info("头条热榜爬取成功: %d 条", len(topics))
                return topics

            logger.error("头条热榜 API 错误: err_no=%s err_msg=%s",
                         result.get("err_no"), result.get("err_msg"))
            return []

        except requests.RequestException as exc:
            logger.error("头条热榜爬取网络异常: %s", exc)
            return []

    # -------------------------------------------------------------------------
    # 发布
    # -------------------------------------------------------------------------

    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布头条文章（直接发布，不经过草稿箱）

        头条文章发布接口: https://open.toutiao.com/docs/api/144
        成功返回 item_id 和文章预览 URL。
        发布前会先验证凭证有效性。
        """
        # 先验证凭证有效性
        validation = self.validate_credentials()
        if not validation["valid"]:
            logger.warning("头条凭证无效，阻止发布: account_id=%s reason=%s",
                          self.account.account_id, validation["reason"])
            return PublishResult(success=False, errmsg=validation["reason"])

        token = self.get_access_token()
        if not token:
            return PublishResult(success=False, errmsg="获取 access_token 失败，请检查 client_key/client_secret")

        try:
            url = "https://open.toutiao.com/douyin/v1/article/create/"
            headers = {
                "Access-Token": token,
                "Content-Type": "application/json",
            }
            # content_type: article=图文，video=视频
            payload = {
                "title": title,
                "content": content,
                "cover_url": cover_url or "",
                "content_type": kwargs.get("content_type", "article"),
            }

            response = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
            result = response.json()

            err_no = result.get("err_no", -1)
            if err_no == 0:
                data = result.get("data", {})
                item_id = data.get("item_id", "")
                article_url = data.get("url", "")
                logger.info(
                    "头条文章发布成功: item_id=%s url=%s title=%s",
                    item_id, article_url, title[:30],
                )
                return PublishResult(
                    success=True,
                    media_id=item_id,
                    url=article_url,
                )

            # IP 白名单错误
            if _is_ip_whitelist_error(err_no):
                _log_ip_error("article/create", result)

            logger.error(
                "头条文章发布失败: err_no=%s err_msg=%s title=%s",
                err_no, result.get("err_msg", ""), title[:30],
            )
            return PublishResult(
                success=False,
                errcode=err_no,
                errmsg=result.get("err_msg", ""),
            )

        except requests.RequestException as exc:
            logger.error("头条文章发布网络异常: %s", exc)
            return PublishResult(success=False, errmsg=str(exc))

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建头条草稿

        头条草稿接口: https://open.toutiao.com/docs/api/144
        返回 draft_id，可用于后续发布或人工编辑。
        """
        token = self.get_access_token()
        if not token:
            return None

        try:
            url = "https://open.toutiao.com/douyin/v1/article/draft/create/"
            headers = {
                "Access-Token": token,
                "Content-Type": "application/json",
            }
            payload = {
                "title": title,
                "content": content,
                "cover_url": cover_url or "",
            }

            response = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
            result = response.json()

            if result.get("err_no") == 0:
                draft_id = result.get("data", {}).get("draft_id", "")
                logger.info("头条草稿创建成功: draft_id=%s title=%s", draft_id, title[:30])
                return {"draft_id": draft_id}

            logger.error(
                "头条草稿创建失败: err_no=%s err_msg=%s",
                result.get("err_no"), result.get("err_msg", ""),
            )
            return None

        except requests.RequestException as exc:
            logger.error("头条草稿创建网络异常: %s", exc)
            return None

    # -------------------------------------------------------------------------
    # 客服消息
    # -------------------------------------------------------------------------

    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送头条客服消息（需用户已授权）"""
        token = self.get_access_token()
        if not token:
            return False

        try:
            url = "https://open.toutiao.com/douyin/v1/message/custom/send/"
            headers = {
                "Access-Token": token,
                "Content-Type": "application/json",
            }
            payload = {
                "open_id": openid,
                "msg_type": "text",
                "content": content,
            }

            response = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
            result = response.json()
            ok = result.get("err_no") == 0
            if not ok:
                logger.warning("头条客服消息发送失败: err_no=%s", result.get("err_no"))
            return ok

        except requests.RequestException as exc:
            logger.error("头条客服消息网络异常: %s", exc)
            return False
