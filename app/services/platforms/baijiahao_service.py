# app/services/platforms/baijiahao_service.py
"""百家号平台服务实现。

百家号是百度旗下内容发布平台，认证使用 BDUSS + ptoken（Cookie 登录态）。
API 接口通过抓包分析获取，无官方公开文档。
"""
import json
import logging
from typing import Dict, Optional, List, Any

import requests

from app.core.redis_client import redis_client
from app.services.platforms.base import PlatformService, PublishResult, CrawledTopic

logger = logging.getLogger(__name__)

# 百家号常见错误码
_ERR_NO_PERMISSION = 10003
_ERR_INVALID_TOKEN = 40101
_ERR_IP_LIMIT = 10204
# 未登录 / Token 失效（百家号常见表现）
_ERR_NOT_LOGIN = (6, 17, 22104)


def _is_ip_limit_error(errno: int) -> bool:
    return errno in {_ERR_IP_LIMIT, _ERR_NO_PERMISSION}


def _is_not_login_error(errno: int) -> bool:
    """判断是否为未登录 / Token 失效错误"""
    return errno in _ERR_NOT_LOGIN


def _log_ip_error(api_name: str, result: dict) -> None:
    logger.error(
        "百家号 API [%s] 触发 IP/权限限制 (errno=%s): %s\n"
        "请检查 BDUSS 是否有效，或是否需要在百度后台配置 IP 白名单。",
        api_name, result.get("errno"), result.get("errmsg", ""),
    )


class BaijiahaoPlatformService(PlatformService):
    """百家号平台服务

    认证方式: BDUSS + ptoken（从百度账号登录后获取）
    注意: BDUSS 是百度账号登录态 Cookie，有有效期限制，需定期刷新。
    """

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)
        self._cache_key = f"baijiahao:bduss:{account.account_id}"
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
            logger.error("Failed to decrypt Baijiahao credentials: %s", exc)
            return {}

    # -------------------------------------------------------------------------
    # 认证
    # -------------------------------------------------------------------------

    def validate_credentials(self) -> Dict[str, Any]:
        """验证 BDUSS 是否有效。

        通过调用百家号用户信息接口判断登录态。
        - 有效：返回 {"valid": True}
        - 无效：返回 {"valid": False, "reason": "BDUSS 已失效，请重新获取并更新平台账号凭证"}
        - 网络错误：返回 {"valid": False, "reason": "网络异常，无法验证凭证"}

        发布前会自动调用，失效时会阻止发布并给出明确提示。
        """
        bduss = self.credentials.get("bduss")
        if not bduss:
            return {"valid": False, "reason": "BDUSS 未配置"}

        try:
            session = self._get_session()
            # 尝试获取百家号账号信息（无需登录态也能拿到部分数据，
            # 但 401/登录错误会明确说明登录失效）
            url = "https://baijiahao.baidu.com/builderinner/api/personal/info"
            response = session.get(url, timeout=self._timeout)
            data = response.json()
            errno = data.get("errno", -1)

            # 明确已登录
            if errno == 0:
                return {"valid": True}

            # 未登录 / Token 失效
            if _is_not_login_error(errno):
                self._invalidate_cache()
                return {
                    "valid": False,
                    "reason": (
                        "BDUSS 已失效（百度账号登录态过期）。"
                        "请重新登录百家号，获取新的 BDUSS 和 ptoken 后在平台账号管理中更新。"
                    ),
                }

            # 其他错误（IP 限制、权限等）
            return {
                "valid": False,
                "reason": f"百家号接口返回错误: errno={errno} errmsg={data.get('errmsg', '')}",
            }

        except requests.RequestException as exc:
            logger.warning("验证百家号凭证网络异常: %s", exc)
            return {"valid": False, "reason": "网络异常，无法验证凭证"}

    def _invalidate_cache(self) -> None:
        """清除 Redis 中缓存的 BDUSS"""
        try:
            redis_client.run_sync(redis_client.delete, self._cache_key)
            logger.info("已清除百家号 BDUSS 缓存: account_id=%s", self.account.account_id)
        except Exception:
            pass

    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取 BDUSS 认证凭证，优先走 Redis 缓存"""
        if not force_refresh:
            cached = redis_client.run_sync(redis_client.get, self._cache_key)
            if cached:
                return cached if isinstance(cached, str) else None

        bduss = self.credentials.get("bduss")
        if not bduss:
            logger.error("Baijiahao BDUSS not configured for account_id=%s",
                         self.account.account_id)
            return None

        # BDUSS 不像 OAuth token 有明确过期时间，但建议缓存
        redis_client.run_sync(redis_client.set, self._cache_key, bduss)
        return bduss

    def _get_session(self) -> requests.Session:
        """获取带认证的 requests Session"""
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://baijiahao.baidu.com/",
        })

        bduss = self.get_access_token()
        if bduss:
            session.cookies.set("BDUSS", bduss, domain=".baidu.com")

        ptoken = self.credentials.get("ptoken")
        if ptoken:
            session.cookies.set("ptoken", ptoken, domain=".baidu.com")

        return session

    # -------------------------------------------------------------------------
    # 热榜
    # -------------------------------------------------------------------------

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取百家号热榜"""
        limit = kwargs.get("limit", 20)

        try:
            session = self._get_session()
            url = (
                "https://baijiahao.baidu.com/"
                "builderinner/api/resource/builderfeed/getBuilderFeedList"
            )
            params = {"num": limit, "page": 1, "tag": "", "type": "hot"}

            response = session.get(url, params=params, timeout=self._timeout)
            if response.status_code != 200:
                logger.warning(
                    "百家号热榜请求失败: status=%s", response.status_code,
                )
                return []

            data = response.json()
            return self._parse_topics(data, limit)

        except requests.RequestException as exc:
            logger.error("百家号热榜网络异常: %s", exc)
            return []

    def _parse_topics(self, data: dict, limit: int) -> List[CrawledTopic]:
        """解析百家号热榜响应"""
        topics = []
        try:
            items = data.get("data", {}).get("list", [])
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                topics.append(CrawledTopic(
                    title=item.get("title", ""),
                    heat_score=float(item.get("hot_score", 0) or 0),
                    source_url=item.get("url", ""),
                    summary=item.get("digest", "")[:200] if item.get("digest") else None,
                    category=item.get("tag", ""),
                    keywords=[],
                    extra_metadata={
                        "author": item.get("author", ""),
                        "article_id": item.get("id", ""),
                    },
                ))
            logger.info("百家号热榜爬取成功: %d 条", len(topics))
        except Exception as exc:
            logger.error("解析百家号热榜响应异常: %s", exc)
        return topics

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
        """发布百家号文章

        成功返回 article_id 和文章预览 URL。
        发布前会先验证 BDUSS 有效性，失效时给出明确提示。
        """
        # 先验证凭证有效性
        validation = self.validate_credentials()
        if not validation["valid"]:
            logger.warning("百家号凭证无效，阻止发布: account_id=%s reason=%s",
                          self.account.account_id, validation["reason"])
            return PublishResult(success=False, errmsg=validation["reason"])

        bduss = self.get_access_token()
        if not bduss:
            return PublishResult(
                success=False,
                errmsg="BDUSS 未配置，请检查平台账号凭证",
            )

        try:
            session = self._get_session()
            url = "https://baijiahao.baidu.com/builderinner/api/content/article/create"

            # 百家号支持 HTML 内容格式
            html_content = f"<h1>{title}</h1><div>{content}</div>"
            payload = {
                "title": title,
                "content": html_content,
                "cover_url": cover_url or "",
                "tags": kwargs.get("tags", []),
                "original": kwargs.get("original", True),
            }

            headers = {"Content-Type": "application/json"}
            response = session.post(url, json=payload, headers=headers, timeout=self._timeout)
            result = response.json()

            errno = result.get("errno", -1)
            if errno == 0:
                data = result.get("data", {})
                article_id = data.get("article_id", "")
                article_url = data.get("url", "")
                logger.info(
                    "百家号文章发布成功: article_id=%s url=%s title=%s",
                    article_id, article_url, title[:30],
                )
                return PublishResult(
                    success=True,
                    media_id=article_id,
                    url=article_url,
                )

            # IP / 权限限制
            if _is_ip_limit_error(errno):
                _log_ip_error("article/create", result)

            logger.error(
                "百家号文章发布失败: errno=%s errmsg=%s title=%s",
                errno, result.get("errmsg", ""), title[:30],
            )
            return PublishResult(
                success=False,
                errcode=errno,
                errmsg=result.get("errmsg", "发布失败"),
            )

        except requests.RequestException as exc:
            logger.error("百家号文章发布网络异常: %s", exc)
            return PublishResult(success=False, errmsg=str(exc))

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建百家号草稿"""
        bduss = self.get_access_token()
        if not bduss:
            return None

        try:
            session = self._get_session()
            url = "https://baijiahao.baidu.com/builderinner/api/content/article/draft/create"

            payload = {
                "title": title,
                "content": content,
                "cover_url": cover_url or "",
            }

            response = session.post(url, json=payload, timeout=self._timeout)
            result = response.json()

            if result.get("errno") == 0:
                draft_id = result.get("data", {}).get("draft_id", "")
                logger.info(
                    "百家号草稿创建成功: draft_id=%s title=%s",
                    draft_id, title[:30],
                )
                return {"draft_id": draft_id}

            logger.error(
                "百家号草稿创建失败: errno=%s errmsg=%s",
                result.get("errno"), result.get("errmsg", ""),
            )
            return None

        except requests.RequestException as exc:
            logger.error("百家号草稿创建网络异常: %s", exc)
            return None

    # -------------------------------------------------------------------------
    # 客服消息（百家号不支持主动消息）
    # -------------------------------------------------------------------------

    def send_customer_message(self, openid: str, content: str) -> bool:
        """百家号不支持主动给用户发消息"""
        logger.warning("百家号不支持主动客服消息")
        return False
