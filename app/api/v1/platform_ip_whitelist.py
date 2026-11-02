# app/api/v1/platform_ip_whitelist.py
"""平台 IP 白名单 CRUD 接口（各平台出站 IP 管理）。

与 /api/v1/ip-whitelist（管理后台访问控制）完全独立。
"""
import ipaddress
import logging
from typing import List

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.auth import require_admin_auth
from app.core.database import get_db
from app.models.platform_ip_whitelist import PlatformIPWhitelist
from app.schemas.platform_ip_whitelist import (
    PLATFORM_TYPES,
    PlatformIPWhitelistCreate,
    PlatformIPWhitelistUpdate,
    PlatformIPWhitelistResponse,
    DetectIPResponse,
    PlatformSummary,
)
from app.schemas.common import DataResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platform-ip-whitelist",
    tags=["platform-ip-whitelist"],
    dependencies=[Depends(require_admin_auth)],
)

_PLATFORM_DISPLAY = {p["value"]: p["label"] for p in PLATFORM_TYPES}
_VALID_PLATFORMS = {p["value"] for p in PLATFORM_TYPES}


def _to_response(row: PlatformIPWhitelist) -> PlatformIPWhitelistResponse:
    return PlatformIPWhitelistResponse(
        id=row.id,
        platform_type=row.platform_type,
        ip_address=row.ip_address,
        label=row.label,
        is_active=row.is_active,
        is_auto_synced=row.is_auto_synced,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_ip(ip_str: str) -> str:
    """校验 IP 格式，返回规范化后的 IP 字符串。"""
    ip_str = ip_str.strip()
    try:
        addr = ipaddress.ip_address(ip_str)
        return str(addr)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"无效的 IP 地址: {ip_str}")


# ---------------------------------------------------------------------------
# 平台汇总（用于前端 tabs）
# ---------------------------------------------------------------------------

@router.get("/platforms", response_model=DataResponse[List[PlatformSummary]])
async def list_platforms(db: Session = Depends(get_db)):
    """返回所有已配置 IP 的平台列表及数量。"""
    rows = (
        db.query(
            PlatformIPWhitelist.platform_type,
            func.count(PlatformIPWhitelist.id).label("ip_count"),
        )
        .filter(PlatformIPWhitelist.is_active.is_(True))
        .group_by(PlatformIPWhitelist.platform_type)
        .all()
    )
    result = [
        PlatformSummary(
            platform_type=r.platform_type,
            display_name=_PLATFORM_DISPLAY.get(r.platform_type, r.platform_type),
            ip_count=r.ip_count,
        )
        for r in rows
    ]
    return DataResponse(data=result)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=DataResponse[List[PlatformIPWhitelistResponse]])
async def list_platform_ip_whitelist(
    platform_type: str = Query(default=None, description="平台类型筛选"),
    db: Session = Depends(get_db),
):
    """获取平台 IP 白名单列表。"""
    query = db.query(PlatformIPWhitelist).order_by(
        PlatformIPWhitelist.created_at.desc()
    )
    if platform_type:
        if platform_type not in _VALID_PLATFORMS:
            raise HTTPException(status_code=422, detail=f"未知平台类型: {platform_type}")
        query = query.filter(PlatformIPWhitelist.platform_type == platform_type)
    rows = query.all()
    return DataResponse(data=[_to_response(r) for r in rows])


@router.post("", response_model=DataResponse[PlatformIPWhitelistResponse])
async def create_platform_ip_whitelist(
    body: PlatformIPWhitelistCreate,
    db: Session = Depends(get_db),
):
    """添加一条平台 IP 白名单记录。"""
    platform = body.platform_type.strip()
    if platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"未知平台类型: {platform}")

    ip_addr = _validate_ip(body.ip_address)

    existing = (
        db.query(PlatformIPWhitelist)
        .filter(
            PlatformIPWhitelist.platform_type == platform,
            PlatformIPWhitelist.ip_address == ip_addr,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail=f"该 IP 已存在于 {platform} 平台")

    row = PlatformIPWhitelist(
        platform_type=platform,
        ip_address=ip_addr,
        label=body.label.strip(),
        is_active=body.is_active,
        is_auto_synced=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return DataResponse(data=_to_response(row))


@router.put("/{record_id}", response_model=DataResponse[PlatformIPWhitelistResponse])
async def update_platform_ip_whitelist(
    record_id: int,
    body: PlatformIPWhitelistUpdate,
    db: Session = Depends(get_db),
):
    """更新平台 IP 白名单记录。"""
    row = db.query(PlatformIPWhitelist).filter(PlatformIPWhitelist.id == record_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")

    if body.ip_address is not None:
        new_ip = _validate_ip(body.ip_address)
        # 检查冲突（同一平台下不能重复 IP）
        conflict = (
            db.query(PlatformIPWhitelist)
            .filter(
                PlatformIPWhitelist.platform_type == row.platform_type,
                PlatformIPWhitelist.ip_address == new_ip,
                PlatformIPWhitelist.id != record_id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(status_code=400, detail="该平台下已存在此 IP")
        row.ip_address = new_ip

    if body.label is not None:
        row.label = body.label.strip()
    if body.is_active is not None:
        row.is_active = body.is_active

    db.commit()
    db.refresh(row)
    return DataResponse(data=_to_response(row))


@router.delete("/{record_id}", response_model=DataResponse)
async def delete_platform_ip_whitelist(
    record_id: int,
    db: Session = Depends(get_db),
):
    """删除平台 IP 白名单记录。"""
    row = db.query(PlatformIPWhitelist).filter(PlatformIPWhitelist.id == record_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(row)
    db.commit()
    return DataResponse(message="已删除")


# ---------------------------------------------------------------------------
# 自动检测本机公网 IP 并同步
# ---------------------------------------------------------------------------

@router.post("/detect-my-ip", response_model=DataResponse[DetectIPResponse])
async def detect_my_ip(db: Session = Depends(get_db)):
    """检测本机公网出口 IP 并自动同步到数据库。

    尝试多个 IP 检测服务，任一成功即返回。
    检测到的 IP 如果在任何平台下已存在，则标记 is_new=False；
    否则自动创建一个 is_auto_synced=True 的记录。
    """
    # 尝试多个 IP 检测服务
    ip_services = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]

    detected_ip = None
    last_error = None
    for url in ip_services:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                text = resp.text.strip()
                # ipify 返回 {"ip": "X.X.X.X"}，其他直接返回 IP 文本
                if text.startswith("{"):
                    import json
                    detected_ip = json.loads(text).get("ip")
                else:
                    detected_ip = text
                if detected_ip:
                    # 校验格式
                    try:
                        ipaddress.ip_address(detected_ip)
                        break
                    except ValueError:
                        detected_ip = None
        except Exception as exc:
            last_error = exc
            continue

    if not detected_ip:
        raise HTTPException(
            status_code=502,
            detail=f"无法获取本机公网 IP，所有服务均失败: {last_error}",
        )

    # 在所有平台记录中查找该 IP 是否已存在
    existing = db.query(PlatformIPWhitelist).filter(
        PlatformIPWhitelist.ip_address == detected_ip
    ).first()

    if existing:
        return DataResponse(
            data=DetectIPResponse(
                ip_address=detected_ip,
                is_new=False,
                record=_to_response(existing),
            )
        )

    # 不存在 → 自动创建一个微信平台的记录（用户可自行修改平台）
    row = PlatformIPWhitelist(
        platform_type="wechat",  # 默认归到微信，用户可手动改
        ip_address=detected_ip,
        label="自动同步",
        is_active=True,
        is_auto_synced=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return DataResponse(
        data=DetectIPResponse(
            ip_address=detected_ip,
            is_new=True,
            record=_to_response(row),
        )
    )
