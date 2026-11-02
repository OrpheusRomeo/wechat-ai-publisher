# app/schemas/llm_provider.py
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict


class LLMProviderBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="显示名称")
    provider_type: str = Field(..., min_length=1, max_length=30, description="类型: deepseek/openai/minimax/qwen")
    api_url: str = Field(..., min_length=1, max_length=255, description="API Base URL")
    api_key: str = Field(..., min_length=1, max_length=255, description="API Key（写入时加密存储）")
    model: str = Field(..., min_length=1, max_length=100, description="模型名")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    timeout: int = Field(default=60, ge=10, le=300, description="超时秒数")
    extra_config: Dict[str, Any] = Field(default_factory=dict, description="扩展配置如 max_tokens")
    is_active: bool = Field(default=True)


class LLMProviderCreate(LLMProviderBase):
    """创建供应商请求"""


class LLMProviderUpdate(BaseModel):
    """更新供应商请求（均 Optional）"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    api_url: Optional[str] = Field(default=None, min_length=1, max_length=255)
    api_key: Optional[str] = Field(default=None, min_length=1, max_length=255)
    model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    timeout: Optional[int] = Field(default=None, ge=10, le=300)
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class LLMProviderResponse(LLMProviderBase):
    """响应中不包含 api_key 明文，只返回掩码供前端展示"""
    api_key: Optional[str] = Field(default=None, exclude=True)
    id: int
    is_default: bool
    is_active: bool
    is_deleted: bool
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMProviderListResponse(BaseModel):
    items: List[LLMProviderResponse]
    total: int
