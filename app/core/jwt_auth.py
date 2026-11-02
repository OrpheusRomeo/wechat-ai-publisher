# app/core/jwt_auth.py
"""JWT 认证模块。

提供:
- ``create_access_token`` / ``create_refresh_token``
- ``verify_token``
- ``get_current_user`` — FastAPI Depends，从 Authorization: Bearer 头提取 JWT 并校验
- ``get_current_active_user`` — 额外校验 is_active

安全修复: refresh token 实现 JTI 黑名单机制 (rotation)。
每次 refresh 后旧 token 的 JTI 被加入 Redis 黑名单，TTL=原 token 剩余寿命，
被盗的旧 token 在黑名单存续期内无法使用。
"""
import logging
import uuid
from datetime import datetime, timezone as tz
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.admin_user import AdminUser

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)

# Redis key 前缀
_JTI_BLACKLIST_PREFIX = "jwt:blacklist:"


def create_access_token(data: dict) -> str:
    """创建 Access Token（短期）。"""
    from datetime import timedelta
    to_encode = data.copy()
    expire = datetime.now(tz.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.effective_jwt_secret, algorithm="HS256")


def create_refresh_token(data: dict) -> str:
    """创建 Refresh Token（长期），内含 JTI 用于旋转后注销。"""
    from datetime import timedelta
    to_encode = data.copy()
    expire = datetime.now(tz.utc) + timedelta(days=settings.refresh_token_expire_days)
    jti = uuid.uuid4().hex
    to_encode.update({"exp": expire, "type": "refresh", "jti": jti})
    return jwt.encode(to_encode, settings.effective_jwt_secret, algorithm="HS256")


def verify_token(token: str) -> Optional[dict]:
    """校验 token 并返回 payload。失败返回 None。"""
    try:
        payload = jwt.decode(
            token, settings.effective_jwt_secret, algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Token 无效: %s", e)
        return None


def verify_refresh_token(token: str) -> Optional[dict]:
    """校验 refresh token，要求 type=refresh，且 JTI 未在黑名单中。"""
    from app.core.redis_client import redis_client

    payload = verify_token(token)
    if payload is None:
        return None
    if payload.get("type") != "refresh":
        return None
    jti = payload.get("jti")
    if not jti:
        # 旧版无 JTI 的 token 拒绝使用，强制重新登录
        logger.warning("收到无 JTI 的 refresh token，拒绝")
        return None
    # 检查黑名单
    blacklisted = redis_client.run_sync(redis_client.get, _JTI_BLACKLIST_PREFIX + jti)
    if blacklisted:
        logger.warning("Refresh token JTI=%s 已在黑名单中（可能被重复使用）", jti)
        return None
    return payload


def revoke_refresh_token(token: str) -> bool:
    """将 refresh token 的 JTI 加入黑名单，TTL=该 token 剩余寿命。

    直接 decode 而不验 exp，因为这个函数只在 refresh 成功后调用，
    此时 token 必然未过期（已通过 verify_refresh_token 检查）。
  """
    from app.core.redis_client import redis_client

    try:
        # 不验签名，只取 exp/jti；签名在 verify_refresh_token 已验过
        payload = jwt.decode(
            token, settings.effective_jwt_secret,
            algorithms=["HS256"], options={"verify_exp": False}
        )
    except jwt.InvalidTokenError:
        return False
    jti = payload.get("jti")
    if not jti:
        return False
    exp = payload.get("exp")
    if not exp:
        return False
    # TTL = 过期时间 - 当前时间，最小 1 秒
    remaining = max(int(exp - datetime.now(tz.utc).timestamp()), 1)
    key = _JTI_BLACKLIST_PREFIX + jti
    redis_client.run_sync(redis_client.set, key, "1", expire=remaining)
    logger.info("Refresh token JTI=%s 已注销，TTL=%ds", jti, remaining)
    return True


async def _get_token_payload(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> dict:
    """从请求头提取并校验 JWT。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_user(
    payload: dict = Depends(_get_token_payload),
) -> AdminUser:
    """获取当前登录用户。"""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 缺少用户标识",
        )
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.id == int(user_id)).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在",
            )
        return user
    finally:
        db.close()


async def get_current_active_user(
    user: AdminUser = Depends(get_current_user),
) -> AdminUser:
    """获取当前活跃用户（is_active=True）。"""
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )
    return user


async def get_current_superadmin(
    user: AdminUser = Depends(get_current_active_user),
) -> AdminUser:
    """获取当前超级管理员（role=superadmin）。"""
    if user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要超级管理员权限",
        )
    return user
