# app/core/auth.py
"""管理后台鉴权依赖。

提供三层鉴权:
- ``require_admin``: 兼容旧的 X-Admin-Token 头 (当 admin_users 表为空时可用)
- ``require_jwt``: JWT Bearer token (新增标准认证)
- ``require_admin_auth``: 合并认证 (JWT 优先，X-Admin-Token 作为 fallback)
- ``require_ip_whitelist``: IP 白名单校验

非 webhook 路由全部应用 ``Depends(require_admin_auth)`` 和
``Depends(require_ip_whitelist)``。

``hmac.compare_digest`` 保证常数时间比较, 防止时序攻击。
"""
import hmac
import ipaddress
import logging
from typing import Optional, List

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# X-Admin-Token (旧版兼容)
# --------------------------------------------------------------------- #

def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """校验管理后台 token; 不通过直接 403。

    用法::

        router = APIRouter(dependencies=[Depends(require_admin)])

    微信回调端点 (/webhook/wechat) 不需要此依赖, 它走的是签名校验。
    """
    expected = settings.admin_api_key
    if not x_admin_token or not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing admin token",
        )
    if not hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid admin token",
        )


# --------------------------------------------------------------------- #
# 合并认证 (JWT 优先 → X-Admin-Token fallback)
# --------------------------------------------------------------------- #

async def require_admin_auth(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """多策略鉴权：优先 JWT Bearer，fallback X-Admin-Token。

    返回 ``{"user_id": int, "username": str, "role": str, "method": "jwt"|"token"}``。
    """
    # 1) 优先 JWT
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from app.core.jwt_auth import verify_token
        from app.models.admin_user import AdminUser
        token = auth_header[7:]
        payload = verify_token(token)
        if payload and payload.get("sub"):
            db = SessionLocal()
            try:
                user = db.query(AdminUser).filter(
                    AdminUser.id == int(payload["sub"])
                ).first()
                if user and user.is_active:
                    return {
                        "user_id": user.id,
                        "username": user.username,
                        "role": user.role,
                        "method": "jwt",
                    }
            finally:
                db.close()

    # 2) Fallback: X-Admin-Token
    expected = settings.admin_api_key
    if x_admin_token and expected:
        if hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
            return {
                "user_id": 0,
                "username": "admin_token",
                "role": "superadmin",
                "method": "token",
            }

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="missing or invalid authentication",
    )


# --------------------------------------------------------------------- #
# IP 白名单
# --------------------------------------------------------------------- #

def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP，支持反向代理 (X-Forwarded-For)。

    只信任 ``settings.trusted_proxies`` 中配置的代理 IP/CIDR。
    修复: 原来用 ``client_host in trusted`` 做字符串精确匹配,
    CIDR (如 ``172.25.0.0/16``) 永远匹配不到单个 IP, 导致 docker
    网络内的合法请求被 IP 白名单拦截。
    """
    trusted_raw = [
        p.strip() for p in settings.trusted_proxies.split(",") if p.strip()
    ]
    if not trusted_raw:
        return request.client.host if request.client else "unknown"

    # 解析信任代理为 ipaddress 网络对象
    trusted_nets = []
    for entry in trusted_raw:
        try:
            trusted_nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            trusted_nets.append(entry)  # 保留原字符串做 fallback

    # 检查请求来源是否是信任的代理 (支持 CIDR)
    client_host = request.client.host if request.client else ""
    is_trusted = False
    try:
        client_addr = ipaddress.ip_address(client_host)
        for net in trusted_nets:
            if isinstance(net, ipaddress.IPv4Network) or isinstance(net, ipaddress.IPv6Network):
                if client_addr in net:
                    is_trusted = True
                    break
            elif net == client_host:
                is_trusted = True
                break
    except ValueError:
        # 无法解析 client_host, fallback 字符串匹配
        if client_host in trusted_raw:
            is_trusted = True

    if not is_trusted:
        return client_host or "unknown"

    # 取 X-Forwarded-For 最左侧的 IP (真实客户端)
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # X-Real-IP fallback
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()

    return client_host or "unknown"


async def require_ip_whitelist(request: Request) -> None:
    """IP 白名单校验。

    - ``settings.app_env == "development"`` 时**自动跳过**: 开发期 docker LAN
      IP 段随主机变化 (重启/换网络), 配 IP 白名单反而反复踩坑; 本地调试
      只需要鉴权 (X-Admin-Token / JWT) 就够. 注意只在 development 时跳过.
    - ``settings.ip_whitelist_enabled=False`` 时跳过 (手动关闭)
    - DB 中启用的白名单为空时放行所有
    - 白名单中有条目时校验客户端 IP 是否匹配 (精确匹配或 CIDR 匹配)
    """
    # 开发环境自动 bypass: 避免 LAN 段配置漂移引发"新机器无法登录"
    if settings.app_env == "development":
        return

    if not settings.ip_whitelist_enabled:
        return

    from app.models.ip_whitelist import IPWhitelist

    db = SessionLocal()
    try:
        rules: List[IPWhitelist] = (
            db.query(IPWhitelist)
            .filter(IPWhitelist.enabled.is_(True))
            .all()
        )
    finally:
        db.close()

    if not rules:
        # 白名单为空 → 允许所有
        return

    client_ip = _get_client_ip(request)
    try:
        client_addr = ipaddress.ip_address(client_ip)
    except ValueError:
        logger.warning("无法解析客户端 IP: %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"无法解析客户端 IP: {client_ip}",
        )

    for rule in rules:
        entry = rule.ip_or_cidr.strip()
        try:
            # 尝试 CIDR 匹配
            network = ipaddress.ip_network(entry, strict=False)
            if client_addr in network:
                return
        except ValueError:
            # 纯 IP 精确匹配
            if entry == client_ip:
                return

    logger.warning("IP 不在白名单: %s", client_ip)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"IP {client_ip} 不在白名单中",
    )
