"""数据模型统一导出。

**必须在这里导入全部 model**，不能只导出一部分。

原因: SQLAlchemy 的 ``relationship("PlatformAccount")`` 用字符串指定目标类，
解析时要求目标类**已经被导入并注册到同一个 registry**。若某个进程只
导入了部分 model（例如只 import 了 ``app.models.topic``），首次执行任何
查询触发 mapper 配置时就会抛:

    InvalidRequestError: When initializing mapper Mapper[HotTopic(hot_topics)],
    expression 'PlatformAccount' failed to locate a name

这个错误在容器里表现为「运行时配置覆盖静默失败」「consumer 收不到消息」，
很难从表面现象定位。所有 model 都在这里导入一次即可保证注册完整。
"""
from app.models.topic import HotTopic, UserChoice, TopicStatus
from app.models.article import GeneratedArticle, ArticleStatus
from app.models.user import UserSubscription
from app.models.admin_user import AdminUser
from app.models.ip_whitelist import IPWhitelist
from app.models.llm_provider import LLMProvider
from app.models.platform import Platform, PlatformAccount, PlatformType
from app.models.platform_ip_whitelist import PlatformIPWhitelist
from app.models.system_setting import SystemSetting

__all__ = [
    # topic
    "HotTopic",
    "UserChoice",
    "TopicStatus",
    # article
    "GeneratedArticle",
    "ArticleStatus",
    # user
    "UserSubscription",
    # admin
    "AdminUser",
    # ip
    "IPWhitelist",
    "PlatformIPWhitelist",
    # llm
    "LLMProvider",
    # platform
    "Platform",
    "PlatformAccount",
    "PlatformType",
    # settings
    "SystemSetting",
]
