# app/services/platforms/__init__.py
"""平台服务工厂"""
from typing import Dict, Type, Optional

from app.services.platforms.base import PlatformService
from app.services.platforms.wechat_service import WeChatPlatformService
from app.services.platforms.douyin_service import DouyinPlatformService
from app.services.platforms.xiaohongshu_service import XiaohongshuPlatformService
from app.services.platforms.toutiao_service import ToutiaoPlatformService
from app.services.platforms.youtube_service import YouTubePlatformService
from app.services.platforms.baijiahao_service import BaijiahaoPlatformService


class PlatformServiceFactory:
    """平台服务工厂，根据平台类型返回对应实现"""

    _services: Dict[str, Type[PlatformService]] = {
        "wechat": WeChatPlatformService,
        "douyin": DouyinPlatformService,
        "xiaohongshu": XiaohongshuPlatformService,
        "toutiao": ToutiaoPlatformService,
        "youtube": YouTubePlatformService,
        "baijiahao": BaijiahaoPlatformService,
    }

    @classmethod
    def register(cls, platform_type: str, service_class: Type[PlatformService]) -> None:
        """注册平台服务"""
        cls._services[platform_type] = service_class

    @classmethod
    def get_service(cls, account: "PlatformAccount") -> Optional[PlatformService]:
        """获取平台服务实例"""
        if not account or not account.platform:
            return None

        platform_type = account.platform.platform_type
        service_class = cls._services.get(platform_type)

        if not service_class:
            raise ValueError(f"Unsupported platform type: {platform_type}")

        return service_class(account)

    @classmethod
    def get_supported_platforms(cls) -> list:
        """获取支持的平台列表"""
        return list(cls._services.keys())

    @classmethod
    def is_supported(cls, platform_type: str) -> bool:
        """检查平台是否支持"""
        return platform_type in cls._services


__all__ = [
    "PlatformService",
    "PlatformServiceFactory",
    "WeChatPlatformService",
    "DouyinPlatformService",
    "XiaohongshuPlatformService",
    "ToutiaoPlatformService",
    "YouTubePlatformService",
]
