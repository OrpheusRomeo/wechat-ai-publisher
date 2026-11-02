# app/api/v1/auth.py
"""认证接口：登录 / 刷新 / 用户信息 / 修改密码 / 用户管理。"""
import logging
from datetime import datetime
from app.core.config import current_datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db, session_scope
from app.core.security import hash_password, verify_password
from app.core.jwt_auth import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    get_current_active_user,
    get_current_superadmin,
)
from app.core.config import settings
from app.core.rate_limit import limiter
from app.models.admin_user import AdminUser
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    UserProfile,
    ChangePasswordRequest,
    CreateUserRequest,
    UpdateUserRequest,
)
from app.schemas.common import DataResponse

logger = logging.getLogger(__name__)


# NOTE: 默认密码检测已移除 -- 不应在源码中硬编码密码明文或哈希

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_to_profile(user: AdminUser) -> UserProfile:
    return UserProfile(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


# ---- 登录 (无需认证) ---- #

@router.post("/login", response_model=DataResponse[TokenResponse])
@limiter.limit("5/minute")  # 防暴力破解
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """管理员登录。"""
    user = db.query(AdminUser).filter(AdminUser.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    # 更新最后登录时间
    user.last_login_at = current_datetime()
    db.commit()

    token_data = {"sub": str(user.id), "username": user.username, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # 检查是否仍在使用初始密码，返回安全警告
    #
    # 注意: bcrypt 每次加盐结果都不同，**不能用 == 比较哈希字符串**
    # (那样永远为 False，警告形同虚设)。必须用 checkpw 校验。
    password_warning = None
    if settings.admin_default_password and verify_password(
        settings.admin_default_password, user.password_hash
    ):
        password_warning = (
            "【安全警告】您正在使用默认密码，存在安全风险。"
            "请立即修改密码：个人资料 → 修改密码"
        )
        logger.warning(
            "用户 %s 仍使用默认密码登录，请提醒其修改",
            user.username,
        )

    response_data = TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=60 * 60,  # 与 settings.access_token_expire_minutes 对应
    )

    # 如果使用默认密码，在 message 中附加警告信息
    response_message = password_warning or "success"

    return DataResponse(
        data=response_data,
        message=response_message,
    )


@router.post("/refresh", response_model=DataResponse[TokenResponse])
async def refresh_token(body: RefreshRequest):
    """刷新 Access Token（refresh token rotation + JTI 黑名单）。"""
    payload = verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token 无效或已过期",
        )
    # 注销旧 token（被盗后重放可被检测）
    revoke_refresh_token(body.refresh_token)
    token_data = {
        "sub": payload.get("sub"),
        "username": payload.get("username"),
        "role": payload.get("role"),
    }
    access_token = create_access_token(token_data)
    refresh_token_new = create_refresh_token(token_data)
    return DataResponse(data=TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_new,
        expires_in=60 * 60,
    ))


# ---- 个人 ---- #

@router.get("/me", response_model=DataResponse[UserProfile])
async def get_me(user: AdminUser = Depends(get_current_active_user)):
    """获取当前登录用户信息。"""
    return DataResponse(data=_user_to_profile(user))


@router.post("/change-password", response_model=DataResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: AdminUser = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """修改当前用户密码。"""
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码错误",
        )
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return DataResponse(message="密码修改成功")


# ---- 用户管理 (仅 superadmin) ---- #

@router.get("/users", response_model=DataResponse[List[UserProfile]])
async def list_users(
    user: AdminUser = Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    """列出所有管理员用户。"""
    users = db.query(AdminUser).order_by(AdminUser.created_at.desc()).all()
    return DataResponse(data=[_user_to_profile(u) for u in users])


@router.post("/users", response_model=DataResponse[UserProfile])
async def create_user(
    body: CreateUserRequest,
    user: AdminUser = Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    """创建新管理员。"""
    existing = db.query(AdminUser).filter(AdminUser.username == body.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"用户名 {body.username} 已存在",
        )
    new_user = AdminUser(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        nickname=body.nickname,
        is_active=body.is_active,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return DataResponse(data=_user_to_profile(new_user))


@router.put("/users/{user_id}", response_model=DataResponse[UserProfile])
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    admin: AdminUser = Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    """修改用户信息。"""
    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.nickname is not None:
        target.nickname = body.nickname
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.password is not None:
        target.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(target)
    return DataResponse(data=_user_to_profile(target))


@router.delete("/users/{user_id}", response_model=DataResponse)
async def delete_user(
    user_id: int,
    admin: AdminUser = Depends(get_current_superadmin),
    db: Session = Depends(get_db),
):
    """删除管理员（不可删除自己）。"""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    target = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(target)
    db.commit()
    return DataResponse(message="删除成功")
