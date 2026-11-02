
# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
from typing import List
from functools import lru_cache

class Settings(BaseSettings):
    # 应用基础配置
    # app_env = "development" 时, require_ip_whitelist 自动 bypass (开发期
    # docker LAN IP 变化无常, 强制白名单反而反复踩坑).
    # 生产/预发请保持 "production" 或 "staging", 此时严格走 ip_whitelist 表.
    app_env: str = Field(default="production")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    # 时区设置：所有时间操作以此时区为准（影响调度、数据分区、日报生成等）
    # 支持 IANA 时区名如 Asia/Shanghai、UTC 等
    timezone: str = Field(default="Asia/Shanghai")
    secret_key: str = Field(..., description="应用密钥")
    app_name: str = Field(default="微信AI文章发布系统")
    app_version: str = Field(default="2.0.0")

    # 管理后台 token (#3): 用于保护 /api/v1/* 端点, 用 X-Admin-Token 头传入
    # 部署时请用 secrets.token_urlsafe(32) 生成, 切勿提交到 git
    # 注意: 当 admin_users 表中有用户时，JWT 优先，X-Admin-Token 作为 fallback
    admin_api_key: str = Field(
        ...,
        min_length=16,
        description="管理后台 API token, 用于校验 X-Admin-Token 头",
    )

    # JWT 认证配置
    jwt_secret_key: str = Field(
        default="",
        description="JWT 签名密钥，留空则使用 secret_key",
    )
    access_token_expire_minutes: int = Field(
        default=60,
        description="Access Token 过期时间（分钟）",
    )
    refresh_token_expire_days: int = Field(
        default=7,
        description="Refresh Token 过期时间（天）",
    )

    # AES 加密密钥（用于加密 DB 中存储的敏感配置，如 AppSecret、DeepSeek Key）
    # 部署时请用: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = Field(
        default="",
        description="Fernet 加密密钥 (url-safe base64 32-byte)，留空则自动生成(重启后已加密数据无法解密!)",
    )

    # IP 白名单
    ip_whitelist_enabled: bool = Field(
        default=True,
        description="是否启用 IP 白名单校验（白名单为空时不拦截）",
    )
    trusted_proxies: str = Field(
        default="",
        description="信任的反向代理 IP, 逗号分隔 (用于 X-Forwarded-For 解析)",
    )

    # 初始管理员密码（仅首次初始化时写入 DB 用，登录后可修改）
    # ⚠️ 安全警告: 默认值 "admin123" 仅限开发/演示环境使用!
    # 生产环境必须通过 ADMIN_DEFAULT_PASSWORD 环境变量设置强密码。
    #
    # 该值同时用于两处:
    # 1. ``alembic`` 初始迁移把 admin 账号的密码哈希成这个值写入
    #    ``admin_users`` 表 (见 ``app/core/security.py:hash_password``)
    # 2. 登录时检测"用户仍在用初始密码"并发出安全警告
    admin_default_password: str = Field(
        default="admin123",
        description="初始管理员密码（首次初始化写入 DB 用）",
    )

    @property
    def is_using_default_password(self) -> bool:
        """检测是否使用了出厂默认密码 (不安全)。"""
        return self.admin_default_password == "admin123"

    # PII 日志脱敏 salt (#10): 不同部署用不同 salt,
    # 跨部署的 openid 散列无法关联, 降低日志聚合后的 PII 风险
    pii_log_salt: str = Field(
        default="change-me-pii-salt",
        min_length=8,
        description="HMAC-SHA256 salt, 用于 openid 日志脱敏",
    )

    # 服务端口
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8888)

    # PostgreSQL
    pg_host: str = Field(default="postgres")
    pg_port: int = Field(default=5555)
    pg_user: str = Field(..., description="PostgreSQL用户名")
    pg_password: str = Field(..., description="PostgreSQL密码")
    pg_db: str = Field(default="wechat_ai_publisher")
    pg_pool_size: int = Field(default=20)
    pg_max_overflow: int = Field(default=40)

    # Redis
    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6666)
    redis_password: str = Field(..., description="Redis密码")
    redis_db: int = Field(default=0)
    redis_celery_db: int = Field(default=1)
    redis_cache_db: int = Field(default=2)

    # Kafka
    kafka_bootstrap_servers: str = Field(default="kafka:9093")
    kafka_consumer_group: str = Field(default="wechat-consumer-group")
    kafka_topic_crawl: str = Field(default="crawl-topics")
    kafka_topic_generate: str = Field(default="generate-articles")
    kafka_topic_push: str = Field(default="push-notifications")
    kafka_topic_publish: str = Field(default="publish-articles")
    # 修复 M12: 死信队列, 持续失败的消息最终发到这里
    kafka_topic_dlq: str = Field(default="wechat-dlq")
    # 修复 M12: 单条消息最大重试次数 (poll 次数), 超过后转 DLQ + commit offset
    kafka_consumer_max_attempts: int = Field(default=5)

    # Celery
    celery_broker_url: str = Field(..., description="Celery Broker URL")
    celery_result_backend: str = Field(..., description="Celery Result Backend")
    celery_timezone: str = Field(default="Asia/Shanghai")
    celery_task_time_limit: int = Field(default=1800)
    celery_worker_concurrency: int = Field(default=4)

    # DeepSeek
    deepseek_api_key: str = Field(..., description="DeepSeek API密钥")
    deepseek_api_url: str = Field(default="https://api.deepseek.com/v1")
    deepseek_model: str = Field(default="deepseek-chat")
    deepseek_temperature: float = Field(default=0.7)
    deepseek_max_tokens: int = Field(default=3000)
    deepseek_timeout: int = Field(default=60)

    # DeepSeek 熔断器：连续失败 threshold 次后 OPEN，recovery_timeout 秒后尝试半开
    cb_deepseek_threshold: int = Field(default=5, description="DeepSeek 熔断触发阈值")
    cb_deepseek_recovery: int = Field(default=60, description="DeepSeek 熔断恢复等待秒数")

    # 微信
    wechat_app_id: str = Field(..., description="微信AppID")
    wechat_app_secret: str = Field(..., description="微信AppSecret")
    wechat_token: str = Field(..., description="微信Token")
    wechat_api_url: str = Field(default="https://api.weixin.qq.com")
    wechat_timeout: int = Field(default=30)

    # WeChat 熔断器
    cb_wechat_threshold: int = Field(default=5, description="WeChat 熔断触发阈值")
    cb_wechat_recovery: int = Field(default=60, description="WeChat 熔断恢复等待秒数")

    # 每日发布配额：订阅号 / 服务号的群发 (freepublish/submit) 每天都有上限。
    # - 个人订阅号: 1
    # - 认证订阅号: 4
    # - 服务号 (认证): 4
    # 超出后微信返回 errcode=45009; 系统应在配额耗尽时把多余文章保存为草稿,
    # 由管理员在公众号后台手动群发。
    wechat_daily_publish_quota: int = Field(
        default=1,
        ge=1,
        description="公众号每日发布配额；个人订阅号 1，认证订阅号 4，服务号 4",
    )
    # 客服消息开关: 未认证个人订阅号没有客服消息权限 (errcode=48001),
    # 设为 false 后 push_tasks / push_consumer 直接跳过客服消息调用,
    # 不刷错误日志, 也不消耗微信 API 配额。
    # 设为 true 才尝试 send_custom_message; 认证订阅号 / 服务号保持 true
    wechat_enable_custom_message: bool = Field(
        default=True,
        description="是否调用 send_custom_message；未认证订阅号置 false",
    )
    # 默认封面图 URL (修复 L12): draft/add 强制要求 thumb_media_id,
    # 没有封面就 40007. 这里配置一张可下载的占位图, 首次发布时自动上传为
    # 永久素材并缓存到 Redis (key=wechat:default_cover_media_id).
    # 推荐用稳定的图片地址 (picsum.photos with seed / 自有 CDN / 项目内 static).
    # 微信推荐封面比例 2.35:1 (900x383), 1:1 (400x400) 会触发 53402 裁剪失败
    wechat_default_cover_url: str = Field(
        default="https://picsum.photos/seed/wechat-digest/900/383",
        description="默认封面图 URL, 微信推荐 2.35:1 比例 (900x383)",
    )

    # 公众号每日 digest 渲染配色 (HTML inline style 用, 接受 #RRGGBB 或 named color)
    # 留空 / unset 时回退到代码里写死的默认色, 不影响启动
    # 默认链接色取 "南京大学紫" (#7D2E8E)
    wechat_digest_color_title: str = Field(
        default="#2C5F8D",
        description="日报 HTML 热搜词颜色, 默认深薄荷蓝",
    )
    wechat_digest_color_brief: str = Field(
        default="#5F8D4E",
        description="日报 HTML 简讯颜色, 默认草色",
    )
    wechat_digest_color_link: str = Field(
        default="#7D2E8E",
        description="日报 HTML 链接颜色, 默认南京大学紫",
    )

    # 调度配置
    scheduler_crawl_time: str = Field(default="08:00")
    scheduler_push_time: str = Field(default="09:00")
    scheduler_topic_limit: int = Field(default=10)

    # 仅在网络断开 / 百度无返回 时才允许回退到 mock (生产应 false)
    baidu_hot_use_mock: bool = Field(default=False, description="爬虫失败时是否回退到内置 mock 数据")

    # 爬虫配置
    crawler_fetch_top_count: int = Field(default=20, description="最多取前几条热搜")
    crawler_news_resolve_candidates: int = Field(default=60, description="解析新闻聚合页时最多抓取候选链接数")

    # 爬虫熔断器
    cb_crawler_threshold: int = Field(default=5, description="爬虫熔断触发阈值")
    cb_crawler_recovery: int = Field(default=30, description="爬虫熔断恢复等待秒数")

    # ============ 业务常量（曾硬编码，现归入配置）============

    # 用户互动 48h 窗口（秒），用于客服消息推送判断
    wechat_message_window_hours: int = Field(default=48, description="客服消息 48h 互动窗口")

    # 推送用户上限（每批次），避免一次查太多用户
    push_user_batch_size: int = Field(default=1000, description="推送时每批次最大用户数")

    # 发布分布式锁 TTL（秒），防止同一 article 并发发布
    publish_lock_ttl_seconds: int = Field(default=600, description="发布锁 TTL 秒数")

    # Celery 任务重试间隔（秒）
    celery_generate_retry_countdown: int = Field(default=120, description="文章生成任务重试间隔")
    celery_publish_retry_countdown: int = Field(default=300, description="文章发布任务重试间隔")
    celery_push_retry_countdown: int = Field(default=600, description="推送任务重试间隔")
    celery_crawl_retry_countdown: int = Field(default=300, description="爬虫任务重试间隔")

    # 功能开关
    enable_crawler: bool = Field(default=True)
    enable_wechat_push: bool = Field(default=True)
    enable_auto_publish: bool = Field(default=False)
    enable_deepseek: bool = Field(default=True)
    enable_kafka: bool = Field(default=True)
    enable_celery: bool = Field(default=True)

    # CORS (字符串形式配置, 避免 pydantic-settings JSON 反序列化报错；
    # ``app/main.py`` 在注入中间件时再调用 ``cors_origins_list``)
    cors_allowed_origins: str = Field(
        default="http://localhost:3333,http://localhost:5173,https://your-domain.com",
        description="逗号分隔的允许来源",
    )

    @property
    def effective_jwt_secret(self) -> str:
        """JWT 签名密钥：优先 jwt_secret_key，否则退回 secret_key。"""
        return self.jwt_secret_key or self.secret_key

    @property
    def effective_fernet_key(self) -> bytes:
        """Fernet 加密密钥：优先 fernet_key，否则退回 secret_key 的 SHA256 派生。
        生产部署强烈建议显式设置 ``FERNET_KEY``。
        """
        import base64
        import hashlib
        if self.fernet_key:
            return self.fernet_key.encode()
        # 退回派生：不能用做真实加密，但至少不会启动失败
        return base64.urlsafe_b64encode(
            hashlib.sha256(self.secret_key.encode()).digest()
        )

    @property
    def cors_origins_list(self) -> List[str]:
        """优先返回标准字段值; 同时支持 ``CORS_ALLOWED_ORIGINS_JSON`` 备选 JSON.
        如果 ``cors_allowed_origins`` 是空字符串, 用兜底默认."""
        raw = self.cors_allowed_origins.strip() if self.cors_allowed_origins else ""
        if not raw:
            return ["http://localhost:3333"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        """支持 JSON 数组 / 逗号分隔 / 单字符串三种格式.

        修复 M3: 之前文件里 _parse_cors 定义了两次, Python 装饰器顺序会导致只有
        最后一个生效, 第一个返回 list 的版本还会撞穿 ``str`` 类型校验. 这里合并
        成一份, 同时接受 list / str 输入并归一化为 str (逗号分隔).
        """
        if v is None or v == "":
            return "http://localhost:3333,https://your-domain.com"
        if isinstance(v, list):
            return ",".join(str(x) for x in v if x)
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return "http://localhost:3333,https://your-domain.com"
            # 兼容 JSON 数组格式 (如 ``["a","b"]``)
            if stripped.startswith("["):
                import json as _json
                try:
                    parsed = _json.loads(stripped)
                except ValueError:
                    return stripped  # 解析失败保留原值
                if isinstance(parsed, list):
                    return ",".join(str(x) for x in parsed if x)
                return str(parsed)
            return stripped
        return str(v)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("celery_broker_url", mode="before")
    @classmethod
    def _validate_celery_broker(cls, v, info):
        if v and isinstance(v, str) and v.startswith("redis://"):
            return v
        password = info.data.get("redis_password", "redis_password")
        host = info.data.get("redis_host", "redis")
        port = info.data.get("redis_port", 6666)
        db = info.data.get("redis_celery_db", 1)
        return f"redis://:{password}@{host}:{port}/{db}"

    @field_validator("celery_result_backend", mode="before")
    @classmethod
    def _validate_celery_backend(cls, v, info):
        if v and isinstance(v, str) and v.startswith("redis://"):
            return v
        password = info.data.get("redis_password", "redis_password")
        host = info.data.get("redis_host", "redis")
        port = info.data.get("redis_port", 6666)
        db = info.data.get("redis_cache_db", 2)
        return f"redis://:{password}@{host}:{port}/{db}"

    @model_validator(mode='after')
    def sync_timezone(self):
        """celery_timezone 与 timezone 保持一致。"""
        self.celery_timezone = self.timezone
        return self

    @model_validator(mode='after')
    def check_production_security(self):
        """生产环境安全检查：敏感配置未设置时警告或失败。"""
        import warnings
        if self.app_env == "production":
            # Fernet key 必须设置，否则加密退化到 SHA256 派生
            if not self.fernet_key:
                warnings.warn(
                    "【安全警告】生产环境未设置 FERNET_KEY，加密退化到 SHA256 派生。"
                    "请设置 FERNET_KEY: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            # JWT secret 为空时使用 secret_key，发出警告
            if not self.jwt_secret_key:
                warnings.warn(
                    "【安全警告】生产环境未设置 JWT_SECRET_KEY，将使用 SECRET_KEY 签名 JWT。"
                    "建议显式设置 JWT_SECRET_KEY"
                )
        return self

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()


def current_datetime() -> "datetime":
    """Return current time (timezone-aware) in settings.timezone.

    All datetime.now() calls should be replaced with this function.
    """
    from datetime import datetime, timedelta, timezone as tz
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(settings.timezone)
    except Exception:
        # fallback: fixed UTC+8
        local_tz = tz(timedelta(hours=8))
    return datetime.now(local_tz)


def today_start() -> "datetime":
    """Return today 00:00:00 (timezone-aware) for date boundary calculations."""
    now = current_datetime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


