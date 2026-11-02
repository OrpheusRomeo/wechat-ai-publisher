# app/schemas/platform_ip_whitelist.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


PLATFORM_TYPES = [
    {"value": "wechat", "label": "微信公众号"},
    {"value": "douyin", "label": "抖音"},
    {"value": "toutiao", "label": "今日头条"},
    {"value": "xiaohongshu", "label": "小红书"},
    {"value": "youtube", "label": "YouTube"},
]


class PlatformIPWhitelistCreate(BaseModel):
    platform_type: str = Field(..., description="平台类型，如 wechat/douyin/toutiao/xiaohongshu/youtube")
    ip_address: str = Field(..., min_length=1, max_length=64, description="公网 IP 地址")
    label: str = Field(default="", max_length=128, description="备注说明")
    is_active: bool = Field(default=True)


class PlatformIPWhitelistUpdate(BaseModel):
    ip_address: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=128)
    is_active: Optional[bool] = None


class PlatformIPWhitelistResponse(BaseModel):
    id: int
    platform_type: str
    ip_address: str
    label: str
    is_active: bool
    is_auto_synced: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DetectIPResponse(BaseModel):
    ip_address: str
    is_new: bool  # True = 本次新增，False = 已存在
    record: Optional[PlatformIPWhitelistResponse] = None


class PlatformSummary(BaseModel):
    platform_type: str
    display_name: str
    ip_count: int
