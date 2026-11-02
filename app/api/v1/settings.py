# app/api/v1/settings.py
"""系统配置管理接口。

GET /settings?category=wechat 返回指定分类的配置（敏感字段显示掩码）
PUT /settings/{key} 更新单项配置（敏感字段传明文或留空保持原值）
PUT /settings/batch 批量更新
"""
import logging
from app.core.config import current_datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_admin_auth, require_ip_whitelist
from app.core.database import get_db
from app.core.runtime_overrides import apply_runtime_overrides
from app.core.security import encrypt_value, decrypt_value, mask_value
from app.models.system_setting import SystemSetting
from app.schemas.setting import SettingItem, SettingUpdate, SettingBatchUpdate
from app.schemas.common import DataResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)],
)


def _to_item(s: SystemSetting) -> SettingItem:
    """将 DB 行转为前端展示结构。"""
    value_display = ""
    if s.is_secret:
        plain = decrypt_value(s.value_encrypted)
        # 值为空时不要沿用种子数据里的占位掩码（那会让人以为凭据已配好）
        value_display = (s.value_masked or mask_value(plain)) if plain else ""
    else:
        value_display = decrypt_value(s.value_encrypted) or s.value_encrypted
    return SettingItem(
        key=s.key,
        value=value_display,
        value_masked=s.value_masked,
        category=s.category,
        description=s.description,
        is_secret=s.is_secret,
        placeholder=s.placeholder,
        updated_at=s.updated_at,
        updated_by=s.updated_by,
    )


def _get_runtime_value(key: str) -> str:
    """从 DB 读取某个配置的运行时值（明文）。

    仅供调试/排查使用；运行时的配置生效走
    :func:`app.core.runtime_overrides.apply_runtime_overrides`，
    它把整表覆盖到 ``settings`` 单例上，而不是逐个 key 现查。
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            return ""
        if row.is_secret:
            return decrypt_value(row.value_encrypted)
        return row.value_encrypted
    finally:
        db.close()


@router.get("", response_model=DataResponse[List[SettingItem]])
async def get_settings(
    category: Optional[str] = Query(default=None, description="分类筛选: wechat/deepseek/scheduler/system"),
    db: Session = Depends(get_db),
):
    """获取系统配置列表。admin 角色可以看到所有配置但敏感值掩码显示。"""
    query = db.query(SystemSetting).order_by(SystemSetting.category, SystemSetting.key)
    if category:
        query = query.filter(SystemSetting.category == category)
    rows = query.all()
    return DataResponse(data=[_to_item(r) for r in rows])


@router.put("/batch", response_model=DataResponse)
async def batch_update_settings(
    body: SettingBatchUpdate,
    auth: dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """批量更新配置。"""
    updated = 0
    for item in body.settings:
        row = db.query(SystemSetting).filter(SystemSetting.key == item.key).first()
        if not row:
            continue
        if row.is_secret and item.value:
            row.value_encrypted = encrypt_value(item.value)
            row.value_masked = mask_value(item.value)
        elif not row.is_secret:
            row.value_encrypted = item.value
        row.updated_at = current_datetime()
        row.updated_by = auth.get("username", "")
        updated += 1
    db.commit()
    # 立即生效：把新值覆盖到运行时 settings（Celery / consumer 进程靠 TTL 缓存同步）
    apply_runtime_overrides(force=True)
    return DataResponse(message=f"已更新 {updated} 项配置")


@router.put("/{key}", response_model=DataResponse[SettingItem])
async def update_setting(
    key: str,
    body: SettingUpdate,
    auth: dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """更新单项配置。"""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"配置项 {key} 不存在")
    # 敏感字段：加密存储
    if row.is_secret:
        if body.value:
            row.value_encrypted = encrypt_value(body.value)
            row.value_masked = mask_value(body.value)
        # 空值不更新，保持原值
    else:
        row.value_encrypted = body.value
    row.updated_at = current_datetime()
    row.updated_by = auth.get("username", "")
    db.commit()
    # 立即生效：把新值覆盖到运行时 settings（Celery / consumer 进程靠 TTL 缓存同步）
    apply_runtime_overrides(force=True)
    db.refresh(row)
    return DataResponse(data=_to_item(row))
