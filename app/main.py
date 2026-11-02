# app/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
from datetime import datetime, timezone, timedelta

_SHANGHAI_TZ = timezone(timedelta(hours=8))

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.database import init_db
from app.core.logging import configure_logging
from app.core.rate_limit import limiter  # #12 全局限流器
from app.core.redis_client import redis_client
from app.core.runtime_overrides import apply_runtime_overrides
from app.api.v1 import topics, articles, webhook, auth, settings as settings_router, ip_whitelist, keyword_articles, tasks, platforms, llm_providers, platform_ip_whitelist

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # 统一日志格式与级别: 不调的话 root logger 没有 handler,
    # 应用自己的 INFO 日志会被静默丢弃 (只看到 WARNING 及以上)。
    configure_logging()
    logger.info("应用启动中...")

    # ⚠️ 安全警告: 检测到默认密码
    if settings.is_using_default_password:
        logger.warning(
            "【安全警告】正在使用默认管理员密码 'admin123'！\n"
            "请立即修改密码：登录管理后台 → 修必个人资料 → 修改密码\n"
            "或在环境变量中设置 ADMIN_DEFAULT_PASSWORD 为强密码"
        )

    init_db()
    await redis_client.get_client()
    # 管理后台「系统配置」写入 system_settings，启动时覆盖到运行时 settings
    apply_runtime_overrides(force=True)
    logger.info("应用启动完成")
    yield
    logger.info("应用关闭中...")
    await redis_client.close()
    logger.info("应用已关闭")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan
)

# 注册限流器 (#12)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def _sync_runtime_settings(request: Request, call_next):
    """每个请求前把管理后台配置同步到运行时 settings。

    为什么需要: uvicorn 以 ``--workers N`` 启动时是 **N 个独立进程**,
    各自持有一份 ``settings`` 单例。管理后台改配置后, 只有处理该次
    ``PUT /settings/{key}`` 请求的那个 worker 会更新, 其余 worker 仍持有
    旧值 → 同一台机器上不同请求读到的配置不一致。

    ``apply_runtime_overrides()`` 自带 15s TTL 缓存, 所以并不会每个请求
    都查库 (最多 15 秒查一次), 代价可忽略。
    """
    try:
        apply_runtime_overrides()
    except Exception:  # noqa: BLE001
        # 配置同步失败不应影响业务请求
        logger.warning("同步 system_settings 到运行时失败", exc_info=True)
    return await call_next(request)


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
# 认证路由（无需 auth 前缀，自管理 prefix）
app.include_router(auth.router)
# IP 白名单路由
app.include_router(ip_whitelist.router)
# 系统配置路由
app.include_router(settings_router.router)
# 业务路由 (保留旧 X-Admin-Token auth 兼容)
app.include_router(topics.router, prefix="/api/v1/topics", tags=["topics"])
app.include_router(articles.router, prefix="/api/v1/articles", tags=["articles"])
# 关键词文章路由
app.include_router(keyword_articles.router)
# 平台账号管理路由
app.include_router(platforms.router)
# Celery 任务状态查询 (供前端轮询)
app.include_router(tasks.router)
# LLM 供应商管理路由
app.include_router(llm_providers.router, prefix="/api/v1", tags=["llm-providers"])
# 平台 IP 白名单路由（各平台出站 IP 管理）
app.include_router(platform_ip_whitelist.router, prefix="/api/v1", tags=["platform-ip-whitelist"])
# 微信 Webhook (无需 auth)
app.include_router(webhook.router, prefix="/webhook", tags=["webhook"])

@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "timestamp": datetime.now(_SHANGHAI_TZ).isoformat(),
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(_SHANGHAI_TZ).isoformat(),
    }

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "内部服务器错误",
            "detail": str(exc) if settings.debug else None
        }
    )

