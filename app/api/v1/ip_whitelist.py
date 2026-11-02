# app/api/v1/ip_whitelist.py
"""IP 白名单 CRUD 接口。"""
import ipaddress
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_admin_auth, require_ip_whitelist
from app.core.database import get_db
from app.models.ip_whitelist import IPWhitelist
from app.schemas.ip_whitelist import (
    IPWhitelistCreate,
    IPWhitelistUpdate,
    IPWhitelistResponse,
    IPCheckResponse,
)
from app.schemas.common import DataResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/ip-whitelist",
    tags=["ip-whitelist"],
    dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)],
)


def _to_response(ip: IPWhitelist) -> IPWhitelistResponse:
    return IPWhitelistResponse(
        id=ip.id,
        ip_or_cidr=ip.ip_or_cidr,
        label=ip.label,
        enabled=ip.enabled,
        created_at=ip.created_at,
        created_by=ip.created_by,
    )


@router.get("", response_model=DataResponse[List[IPWhitelistResponse]])
async def list_ip_whitelist(
    enabled_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """获取 IP 白名单列表。"""
    query = db.query(IPWhitelist).order_by(IPWhitelist.created_at.desc())
    if enabled_only:
        query = query.filter(IPWhitelist.enabled.is_(True))
    rows = query.all()
    return DataResponse(data=[_to_response(r) for r in rows])


@router.post("", response_model=DataResponse[IPWhitelistResponse])
async def create_ip_whitelist(
    body: IPWhitelistCreate,
    auth: dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """添加 IP 白名单条目。"""
    # 校验 IP/CIDR 格式
    entry = body.ip_or_cidr.strip()
    try:
        ipaddress.ip_network(entry, strict=False)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"无效的 IP 或 CIDR 格式: {entry}",
        )
    existing = db.query(IPWhitelist).filter(IPWhitelist.ip_or_cidr == entry).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"{entry} 已在白名单中")

    row = IPWhitelist(
        ip_or_cidr=entry,
        label=body.label,
        enabled=body.enabled,
        created_by=auth.get("username", ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return DataResponse(data=_to_response(row))


@router.put("/{rule_id}", response_model=DataResponse[IPWhitelistResponse])
async def update_ip_whitelist(
    rule_id: int,
    body: IPWhitelistUpdate,
    db: Session = Depends(get_db),
):
    """更新 IP 白名单条目。"""
    row = db.query(IPWhitelist).filter(IPWhitelist.id == rule_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="白名单条目不存在")

    if body.ip_or_cidr is not None:
        try:
            ipaddress.ip_network(body.ip_or_cidr, strict=False)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"无效的 IP 或 CIDR 格式: {body.ip_or_cidr}")
        row.ip_or_cidr = body.ip_or_cidr
    if body.label is not None:
        row.label = body.label
    if body.enabled is not None:
        row.enabled = body.enabled
    db.commit()
    db.refresh(row)
    return DataResponse(data=_to_response(row))


@router.delete("/{rule_id}", response_model=DataResponse)
async def delete_ip_whitelist(
    rule_id: int,
    db: Session = Depends(get_db),
):
    """删除 IP 白名单条目。"""
    row = db.query(IPWhitelist).filter(IPWhitelist.id == rule_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="白名单条目不存在")
    db.delete(row)
    db.commit()
    return DataResponse(message="已删除")


@router.post("/check", response_model=DataResponse[IPCheckResponse])
async def check_ip(
    ip: str = Query(..., description="要检测的 IP 地址"),
    db: Session = Depends(get_db),
):
    """检测指定 IP 是否在白名单中（用于调试）。"""
    try:
        client = ipaddress.ip_address(ip.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"无效的 IP: {ip}")

    rules = (
        db.query(IPWhitelist)
        .filter(IPWhitelist.enabled.is_(True))
        .all()
    )
    if not rules:
        return DataResponse(data=IPCheckResponse(ip=str(client), allowed=True, message="白名单为空，默认允许"))

    for rule in rules:
        try:
            net = ipaddress.ip_network(rule.ip_or_cidr, strict=False)
            if client in net:
                return DataResponse(data=IPCheckResponse(ip=str(client), allowed=True, message=f"匹配规则: {rule.ip_or_cidr} ({rule.label})"))
        except ValueError:
            if rule.ip_or_cidr == str(client):
                return DataResponse(data=IPCheckResponse(ip=str(client), allowed=True, message=f"匹配规则: {rule.ip_or_cidr} ({rule.label})"))

    return DataResponse(data=IPCheckResponse(ip=str(client), allowed=False, message="未匹配任何白名单规则"))
