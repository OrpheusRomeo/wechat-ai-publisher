# app/services/platforms/base.py
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PublishResult:
    """统一的发布结果"""
    success: bool
    media_id: Optional[str] = None
    publish_id: Optional[str] = None
    errcode: Optional[int] = None
    errmsg: Optional[str] = None
    url: Optional[str] = None


@dataclass
class CrawledTopic:
    """统一的爬取话题格式"""
    title: str
    heat_score: float
    source_url: str
    summary: Optional[str] = None
    category: Optional[str] = None
    keywords: List[str] = None
    extra_metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []
        if self.extra_metadata is None:
            self.extra_metadata = {}


class PlatformService(ABC):
    """平台服务抽象基类"""

    def __init__(self, account: "PlatformAccount"):
        from app.models.platform import PlatformAccount as PlatformAccountModel
        # 延迟导入避免循环依赖
        self.account = account
        self._credentials: Optional[Dict[str, Any]] = None

    @property
    def credentials(self) -> Dict[str, Any]:
        """解密后的凭证"""
        if self._credentials is None:
            self._credentials = self._decrypt_credentials()
        return self._credentials

    @abstractmethod
    def _decrypt_credentials(self) -> Dict[str, Any]:
        """从 encrypted 凭证解密出 dict"""
        pass

    @abstractmethod
    def get_access_token(self) -> Optional[str]:
        """获取平台访问令牌"""
        pass

    @abstractmethod
    def publish_article(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> PublishResult:
        """发布文章"""
        pass

    @abstractmethod
    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """创建草稿"""
        pass

    @abstractmethod
    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送客服消息"""
        pass

    @abstractmethod
    def crawl_topics(self, **kwargs) -> List[CrawledTopic]:
        """爬取平台热榜话题"""
        pass

    def validate_credentials(self) -> bool:
        """验证凭证是否有效（子类可override）"""
        return True

    def get_platform_type(self) -> str:
        """获取平台类型"""
        return self.account.platform.platform_type if self.account.platform else ""
