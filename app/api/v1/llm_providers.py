# app/api/v1/llm_providers.py
"""LLM 供应商管理 API"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import require_admin_auth
from app.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderUpdate,
    LLMProviderResponse,
    LLMProviderListResponse,
)
from app.services.llm_provider import LLMProviderService
from app.schemas.common import DataResponse, ListResponse

router = APIRouter(prefix="/llm-providers", tags=["LLM供应商管理"])


@router.get("/", response_model=DataResponse[LLMProviderListResponse])
async def list_providers(
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """获取所有 LLM 供应商列表"""
    svc = LLMProviderService(db)
    providers = svc.get_providers()
    return DataResponse(
        data=LLMProviderListResponse(
            items=[LLMProviderResponse.model_validate(p) for p in providers],
            total=len(providers),
        )
    )


@router.get("/default", response_model=DataResponse[LLMProviderResponse])
async def get_default_provider(
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """获取当前默认供应商（不含 api_key 明文）"""
    svc = LLMProviderService(db)
    provider = svc.get_default_provider()
    if not provider:
        raise HTTPException(status_code=404, detail="未设置默认供应商，请先添加并设为默认")
    return DataResponse(data=LLMProviderResponse.model_validate(provider))


@router.get("/{provider_id}", response_model=DataResponse[LLMProviderResponse])
async def get_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """获取指定供应商详情"""
    svc = LLMProviderService(db)
    provider = svc.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return DataResponse(data=LLMProviderResponse.model_validate(provider))


@router.post("/", response_model=DataResponse[LLMProviderResponse])
async def create_provider(
    data: LLMProviderCreate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """添加新的 LLM 供应商"""
    svc = LLMProviderService(db)
    provider = svc.create_provider(data.model_dump())
    return DataResponse(
        code=0, message="添加成功", data=LLMProviderResponse.model_validate(provider)
    )


@router.put("/{provider_id}", response_model=DataResponse[LLMProviderResponse])
async def update_provider(
    provider_id: int,
    data: LLMProviderUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """更新供应商配置（api_key 留空则不更新）"""
    svc = LLMProviderService(db)
    provider = svc.update_provider(provider_id, data.model_dump(exclude_unset=True))
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return DataResponse(
        code=0, message="更新成功", data=LLMProviderResponse.model_validate(provider)
    )


@router.delete("/{provider_id}", response_model=DataResponse)
async def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """删除供应商（软删除）"""
    svc = LLMProviderService(db)
    ok = svc.delete_provider(provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return DataResponse(code=0, message="删除成功")


@router.put("/{provider_id}/set-default", response_model=DataResponse[LLMProviderResponse])
async def set_default_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin_auth),
):
    """将指定供应商设为默认"""
    svc = LLMProviderService(db)
    provider = svc.set_default(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return DataResponse(
        code=0, message="已设为默认", data=LLMProviderResponse.model_validate(provider)
    )
