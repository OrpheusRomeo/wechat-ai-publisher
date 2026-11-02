# app/services/platforms/xiaohongshu_service.py
"""小红书平台服务实现。

小红书开放平台提供了创作者 API，但需要申请资质。
非官方方式可以通过爬虫获取热榜内容。
"""
import json
import logging
import hashlib
import time
from typing import Dict, Optional, List, Any

import requests

from app.services.platforms.base import PlatformService, PublishResult, CrawledTopic

logger = logging.getLogger(__name__)


class XiaohongshuPlatformService(PlatformService):
    """小红书平台服务

    注意：小红书发布 API 需要创作者资质认证，个人用户较难申请。
    目前实现为预留接口，实际使用需要：
    1. 申请小红书创作者 API 资质
    2. 或者通过第三方平台（如新榜）间接发布
    """

    def __init__(self, account: "PlatformAccount") -> None:
        super().__init__(account)
        self._session = requests.Session()

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
            logger.error("Failed to decrypt Xiaohongshu credentials: %s", exc)
            return {}

    def get_access_token(self) -> Optional[str]:
        """获取访问令牌

        小红书 API 使用 xhs_token 作为访问凭证
        """
        token = self.credentials.get("xhs_token")
        return token if token else None

    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取小红书热榜

        通过搜索接口获取热门内容
        """
        limit = kwargs.get("limit", 20)

        # 小红书热榜 API（可能需要登录态）
        # 尝试多个可能的端点
        endpoints = [
            "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes",
            "https://www.xiaohongshu.com/explore/rank",
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.xiaohongshu.com/",
        }

        # 添加 cookie 如果有的话
        xhs_token = self.credentials.get("xhs_token")
        if xhs_token:
            headers["Cookie"] = f"xhs_token={xhs_token}"

        for endpoint in endpoints:
            try:
                response = requests.get(endpoint, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    topics = self._parse_xhs_response(data, limit)
                    if topics:
                        return topics
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {e}")
                continue

        # 如果 API 失败，返回空列表
        logger.warning("Xiaohongshu hot search API not available")
        return []

    def _parse_xhs_response(self, data: dict, limit: int) -> List[CrawledTopic]:
        """解析小红书 API 响应"""
        topics = []

        try:
            # 尝试解析不同格式的响应
            items = data.get("data", {}).get("items", [])
            if not items:
                items = data.get("data", [])

            for item in items[:limit]:
                if isinstance(item, dict):
                    topics.append(CrawledTopic(
                        title=item.get("title", ""),
                        heat_score=float(item.get("like_count", 0) or 0),
                        source_url=item.get("share_url", ""),
                        summary=item.get("desc", "")[:200] if item.get("desc") else None,
                        category=item.get("tag", ""),
                        keywords=item.get("topics", []),
                        extra_metadata={
                            "note_id": item.get("note_id", ""),
                            "user": item.get("user", {}).get("nickname", ""),
                        },
                    ))
        except Exception as e:
            logger.error(f"Failed to parse Xiaohongshu response: {e}")

        return topics

    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布小红书笔记

        注意：小红书官方 API 需要创作者资质，个人版本较难申请。
        发布前会先验证 xhs_token 有效性。
        """
        # 先验证凭证有效性
        validation = self.validate_credentials()
        if not validation["valid"]:
            logger.warning("小红书凭证无效，阻止发布: account_id=%s reason=%s",
                          self.account.account_id, validation["reason"])
            return PublishResult(success=False, errmsg=validation["reason"])

        # 检查是否有发布权限（仅验证 token 存在）
        if not self._can_publish():
            return PublishResult(
                success=False,
                errmsg=(
                    "小红书发布 API 需要创作者资质。"
                    "请在 creators.xiaohongshu.com 申请资质，或使用第三方平台代理发布。"
                ),
            )

        access_token = self.get_access_token()
        if not access_token:
            return PublishResult(success=False, errmsg="xhs_token not configured")

        api_url = "https://edith.xiaohongshu.com/api/sns/web/v1/media/upload"

        try:
            # 构建笔记数据
            payload = {
                "title": title,
                "content": content,
                "cover": cover_url,
                "note_type": "normal",  # or "video"
            }

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
            result = response.json()

            if result.get("success"):
                return PublishResult(
                    success=True,
                    media_id=result.get("data", {}).get("note_id", ""),
                    url=result.get("data", {}).get("url", ""),
                )
            else:
                return PublishResult(
                    success=False,
                    errcode=result.get("code", -1),
                    errmsg=result.get("msg", "Unknown error"),
                )

        except Exception as exc:
            logger.error(f"Xiaohongshu publish error: {exc}")
            return PublishResult(success=False, errmsg=str(exc))

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建小红书草稿"""
        # 小红书没有草稿概念，返回 None
        return None

    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送小红书消息

        小红书不支持主动给用户发消息，只能用户主动触达
        """
        logger.warning("Xiaohongshu does not support proactive customer messages")
        return False

    def _can_publish(self) -> bool:
        """检查是否可以发布"""
        xhs_token = self.credentials.get("xhs_token")
        return bool(xhs_token)

    def validate_credentials(self) -> Dict[str, Any]:
        """验证 xhs_token 是否有效。

        通过调用小红书用户信息接口判断登录态。
        - 有效：返回 {"valid": True}
        - 无效：返回 {"valid": False, "reason": "xhs_token 已失效，请重新获取并更新平台账号凭证"}
        - 网络错误：返回 {"valid": False, "reason": "网络异常，无法验证凭证"}

        注意：小红书即使 token 有效，也需要创作者资质才能发布。
        """
        xhs_token = self.credentials.get("xhs_token")
        if not xhs_token:
            return {"valid": False, "reason": "xhs_token 未配置，请检查平台账号凭证"}

        try:
            session = self._get_session()
            # 尝试获取用户信息接口验证登录态
            url = "https://edith.xiaohongshu.com/api/sns/web/v1/user/selfinfo"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://www.xiaohongshu.com/",
                "Cookie": f"xhs_token={xhs_token}",
            }
            response = session.get(url, headers=headers, timeout=15)
            data = response.json()

            # 小红书 errcode: 0 = 成功
            if data.get("errcode") == 0:
                return {"valid": True}

            # 登录态失效
            errcode = data.get("errcode", -1)
            errmsg = data.get("errmsg", "")
            logger.warning(
                "小红书 xhs_token 验证失败: errcode=%s errmsg=%s",
                errcode, errmsg,
            )
            return {
                "valid": False,
                "reason": (
                    "xhs_token 已失效（小红书登录态过期）。"
                    "请重新在浏览器登录小红书，获取新的 xhs_token 后在平台账号管理中更新。"
                ),
            }

        except requests.RequestException as exc:
            logger.warning("验证小红书凭证网络异常: %s", exc)
            return {"valid": False, "reason": "网络异常，无法验证凭证"}
