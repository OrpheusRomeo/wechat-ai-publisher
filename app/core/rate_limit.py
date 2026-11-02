# app/core/rate_limit.py
"""全局限流器 (#12)。

为避免 ``app/main.py`` 与 ``app/api/v1/articles.py`` 互相 import,
限流器单独放在 ``app.core`` 下, main 注入到 app.state, 端点直接
``from app.core.rate_limit import limiter``。
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

from app.core.config import settings


def _get_client_ip_for_limiter(request: Request) -> str:
    """获取客户端真实 IP，支持反向代理场景。

    解析顺序:
    1. X-Forwarded-For 头 (最左侧的 IP 是原始客户端 IP)
    2. X-Real-IP 头
    3. request.client.host 作为 fallback

    安全说明:
    - 只有在 trusted_proxies 中配置的代理 IP 才信任其 X-Forwarded-For 头
    - 如果未配置 trusted_proxies，不信任任何 X-Forwarded-For，防止 IP 欺骗
    - 开发环境（APP_ENV=development）自动放行所有请求
    """
    # 开发环境不进行 IP 检查
    if settings.app_env == "development":
        return request.client.host if request.client else "unknown"

    # 获取信任的代理 IP 列表
    trusted_ips = set()
    if settings.trusted_proxies:
        trusted_ips = {ip.strip() for ip in settings.trusted_proxies.split(",") if ip.strip()}

    # 检查 X-Forwarded-For
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # X-Forwarded-For 格式: "client, proxy1, proxy2, ..."
        # 最左侧是原始客户端 IP
        client_ip = x_forwarded_for.split(",")[0].strip()

        # 必须配置了信任代理且直接连接的代理在信任列表中，才使用 X-Forwarded-For
        if trusted_ips and request.client and request.client.host in trusted_ips:
            return client_ip

        # 未配置 trusted_proxies 时，不信任 X-Forwarded-For，防止 IP 欺骗
        # 直接使用 request.client.host
        if request.client:
            return request.client.host
        return "unknown"

    # 检查 X-Real-IP
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()

    # Fallback: 使用 request.client.host
    if request.client:
        return request.client.host

    return "unknown"


# 用自定义 IP 获取函数，支持反向代理
limiter = Limiter(key_func=_get_client_ip_for_limiter, default_limits=[])