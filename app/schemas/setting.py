# app/schemas/setting.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SettingItem(BaseModel):
    key: str
    value: str = ""
    value_masked: str = ""
    category: str = "system"
    description: str = ""
    is_secret: bool = False
    placeholder: str = ""
    updated_at: Optional[datetime] = None
    updated_by: str = ""

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    value: str = Field(..., description="新值（敏感字段传明文，后端加密存储）")


class SettingBatchUpdateItem(BaseModel):
    """批量更新时单条配置项：只需要 key + value。其他字段由后端自动补全。"""
    key: str = Field(..., description="配置键")
    value: str = Field(default="", description="新值")


class SettingBatchUpdate(BaseModel):
    settings: List[SettingBatchUpdateItem] = Field(default=[], max_length=50)
