# app/api/v1/platforms.py
"""平台和账号管理 API"""
import json
import logging
from datetime import datetime
from app.core.config import current_datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import require_admin_auth, require_ip_whitelist
from app.core.config import settings
from app.models.platform import Platform, PlatformAccount, PlatformType as PlatformTypeEnum
from app.schemas.platform import (
    PlatformCreate, PlatformUpdate, PlatformResponse,
    PlatformAccountCreate, PlatformAccountUpdate, PlatformAccountResponse,
    PlatformAccountListResponse, PlatformAccountScheduleUpdate,
    PlatformAccountRestoreResponse, PlatformType,
)
from app.schemas.common import DataResponse, ListResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/platforms", tags=["platforms"])


def _encrypt_credentials(credentials: dict) -> tuple:
    """加密凭证，返回 (encrypted, masked)。

    加密失败时抛出异常，因为存储空加密值会导致凭证数据损坏。
    """
    from cryptography.fernet import Fernet, InvalidToken

    masked = {}
    masked_display = {}

    for key, value in credentials.items():
        if value:
            if key == "app_secret" or key == "client_secret" or key == "xhs_token":
                masked_display[key] = value[:4] + "****" + value[-4:] if len(value) > 8 else "****"
            else:
                masked_display[key] = value

    try:
        fernet = Fernet(settings.effective_fernet_key)
        encrypted = fernet.encrypt(json.dumps(credentials, ensure_ascii=False).encode()).decode()
    except (InvalidToken, Exception) as exc:
        logger.error("加密凭证失败: %s", exc)
        raise ValueError(f"加密凭证失败: {exc}") from exc

    return encrypted, masked_display


# ============ Platform Endpoints ============

@router.get("", response_model=ListResponse[PlatformResponse])
async def list_platforms(
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取平台列表"""
    query = db.query(Platform)
    if is_active is not None:
        query = query.filter(Platform.is_active == is_active)

    platforms = query.order_by(Platform.id).all()
    return ListResponse(
        data=[PlatformResponse.model_validate(p) for p in platforms],
        total=len(platforms),
    )


@router.post("", response_model=DataResponse[PlatformResponse])
async def create_platform(
    data: PlatformCreate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """创建平台"""
    existing = db.query(Platform).filter(Platform.platform_type == data.platform_type).first()
    if existing:
        raise HTTPException(status_code=400, detail="Platform type already exists")

    platform = Platform(
        platform_type=data.platform_type,
        display_name=data.display_name,
        icon=data.icon or "",
        is_active=data.is_active,
    )
    db.add(platform)
    db.commit()
    db.refresh(platform)

    return DataResponse(data=PlatformResponse.model_validate(platform))


# ============ PlatformAccount Endpoints ============
# 注意: /accounts 路由必须在 /{platform_type} 之前定义，
# 否则 /accounts 会被 FastAPI 匹配为 platform_type="accounts"


@router.get("/accounts", response_model=PlatformAccountListResponse)
async def list_accounts(
    platform_type: Optional[str] = None,
    include_deleted: bool = False,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取账号列表"""
    query = db.query(PlatformAccount).join(Platform)

    if platform_type:
        query = query.filter(Platform.platform_type == platform_type)
    if not include_deleted:
        query = query.filter(PlatformAccount.is_deleted == False)
    if is_active is not None:
        query = query.filter(PlatformAccount.is_active == is_active)

    accounts = query.order_by(PlatformAccount.created_at.desc()).all()
    return PlatformAccountListResponse(
        items=[PlatformAccountResponse.model_validate(a) for a in accounts],
        total=len(accounts),
    )


@router.post("/accounts", response_model=DataResponse[PlatformAccountResponse])
async def create_account(
    data: PlatformAccountCreate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """创建账号"""
    platform = db.query(Platform).filter(Platform.platform_type == data.platform_type.value).first()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform {data.platform_type} not found")

    credentials_dict = data.credentials.model_dump(exclude_none=True)
    try:
        encrypted, masked = _encrypt_credentials(credentials_dict)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"加密凭证失败: {exc}")

    account = PlatformAccount(
        platform_id=platform.id,
        account_name=data.account_name,
        credentials_encrypted=encrypted,
        credentials_masked=masked,
        crawl_cron=data.crawl_cron,
        push_cron=data.push_cron,
        config=data.config or {},
        is_active=True,
        is_deleted=False,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return DataResponse(data=PlatformAccountResponse.model_validate(account))


@router.get("/accounts/{account_id}", response_model=DataResponse[PlatformAccountResponse])
async def get_account(
    account_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取账号详情"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return DataResponse(data=PlatformAccountResponse.model_validate(account))


@router.put("/accounts/{account_id}", response_model=DataResponse[PlatformAccountResponse])
async def update_account(
    account_id: UUID,
    data: PlatformAccountUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """更新账号"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot update deleted account")

    if data.account_name is not None:
        account.account_name = data.account_name
    if data.crawl_cron is not None:
        account.crawl_cron = data.crawl_cron
    if data.push_cron is not None:
        account.push_cron = data.push_cron
    if data.config is not None:
        account.config = data.config
    if data.is_active is not None:
        account.is_active = data.is_active

    if data.credentials is not None:
        credentials_dict = data.credentials.model_dump(exclude_none=True)
        try:
            encrypted, masked = _encrypt_credentials(credentials_dict)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"加密凭证失败: {exc}")
        account.credentials_encrypted = encrypted
        account.credentials_masked = masked

    db.commit()
    db.refresh(account)
    return DataResponse(data=PlatformAccountResponse.model_validate(account))


@router.delete("/accounts/{account_id}", response_model=DataResponse)
async def soft_delete_account(
    account_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """软删除账号"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.is_deleted:
        raise HTTPException(status_code=400, detail="Account already deleted")

    account.is_deleted = True
    account.deleted_at = current_datetime()
    account.is_active = False
    db.commit()

    return DataResponse(data={"account_id": str(account_id), "message": "Account soft deleted"})


@router.post("/accounts/{account_id}/restore", response_model=DataResponse[PlatformAccountRestoreResponse])
async def restore_account(
    account_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """恢复账号"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.is_deleted:
        raise HTTPException(status_code=400, detail="Account is not deleted")

    account.is_deleted = False
    account.deleted_at = None
    account.is_active = True
    db.commit()
    db.refresh(account)

    return DataResponse(data=PlatformAccountRestoreResponse(
        id=account.id,
        account_id=account.account_id,
        account_name=account.account_name,
        is_deleted=account.is_deleted,
        is_active=account.is_active,
    ))


@router.delete("/accounts/{account_id}/permanent", response_model=DataResponse)
async def permanent_delete_account(
    account_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """永久删除账号（级联删除关联数据）"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.is_deleted:
        raise HTTPException(status_code=400, detail="Must soft delete first before permanent delete")

    db.delete(account)
    db.commit()

    return DataResponse(data={"account_id": str(account_id), "message": "Account permanently deleted"})


@router.patch("/accounts/{account_id}/schedule", response_model=DataResponse[PlatformAccountResponse])
async def update_account_schedule(
    account_id: UUID,
    data: PlatformAccountScheduleUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """更新账号调度时间"""
    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.is_deleted:
        raise HTTPException(status_code=400, detail="Cannot update deleted account")

    if data.crawl_cron is not None:
        account.crawl_cron = data.crawl_cron
    if data.push_cron is not None:
        account.push_cron = data.push_cron

    db.commit()
    db.refresh(account)
    return DataResponse(data=PlatformAccountResponse.model_validate(account))


# ============ Account Data Endpoints ============


@router.get("/accounts/{account_id}/topics", response_model=ListResponse)
async def get_account_topics(
    account_id: UUID,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取账号的话题列表"""
    from app.models.topic import HotTopic

    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    query = db.query(HotTopic).filter(HotTopic.platform_account_id == account.id)
    if status:
        query = query.filter(HotTopic.status == status)

    total = query.count()
    topics = query.order_by(HotTopic.create_time.desc()).offset((page - 1) * page_size).limit(page_size).all()

    from app.schemas.topic import TopicResponse

    return ListResponse(
        data=[TopicResponse.model_validate(t) for t in topics],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/accounts/{account_id}/articles", response_model=ListResponse)
async def get_account_articles(
    account_id: UUID,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取账号的文章列表"""
    from app.models.article import GeneratedArticle
    from app.schemas.article import ArticleResponse

    account = db.query(PlatformAccount).filter(PlatformAccount.account_id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    query = db.query(GeneratedArticle).filter(GeneratedArticle.platform_account_id == account.id)
    if status:
        query = query.filter(GeneratedArticle.status == status)

    total = query.count()
    articles = query.order_by(GeneratedArticle.create_time.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return ListResponse(
        data=[ArticleResponse.model_validate(a) for a in articles],
        total=total,
        page=page,
        page_size=page_size,
    )


# ============ Platform CRUD (/{platform_type} 必须放在 /accounts 之后) ============


@router.get("/{platform_type}", response_model=DataResponse[PlatformResponse])
async def get_platform(
    platform_type: str,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """获取平台详情"""
    platform = db.query(Platform).filter(Platform.platform_type == platform_type).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")
    return DataResponse(data=PlatformResponse.model_validate(platform))


@router.put("/{platform_type}", response_model=DataResponse[PlatformResponse])
async def update_platform(
    platform_type: str,
    data: PlatformUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
    __=Depends(require_ip_whitelist),
):
    """更新平台"""
    platform = db.query(Platform).filter(Platform.platform_type == platform_type).first()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")

    if data.display_name is not None:
        platform.display_name = data.display_name
    if data.icon is not None:
        platform.icon = data.icon
    if data.is_active is not None:
        platform.is_active = data.is_active

    db.commit()
    db.refresh(platform)
    return DataResponse(data=PlatformResponse.model_validate(platform))
