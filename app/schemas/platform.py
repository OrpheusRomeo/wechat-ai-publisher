# app/schemas/platform.py
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

from app.schemas.common import _serialize_datetime


class PlatformType(str, Enum):
    WECHAT = "wechat"
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BAIJIAHAO = "baijiahao"
    TOUTIAO = "toutiao"
    YOUTUBE = "youtube"


# ============ Platform Schemas ============

class PlatformBase(BaseModel):
    platform_type: str = Field(..., max_length=32, description="平台类型")
    display_name: str = Field(..., max_length=64, description="显示名称")
    icon: Optional[str] = Field("", max_length=128, description="图标URL")
    is_active: bool = Field(True, description="是否启用")


class PlatformCreate(PlatformBase):
    pass


class PlatformUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=64)
    icon: Optional[str] = Field(None, max_length=128)
    is_active: Optional[bool] = None


class PlatformResponse(PlatformBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: _serialize_datetime},
    )


# ============ PlatformAccount Schemas ============

class AccountCredentials(BaseModel):
    """各平台凭证格式"""
    # WeChat
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    token: Optional[str] = None
    encoding_aes_key: Optional[str] = None
    # Douyin
    client_key: Optional[str] = None
    client_secret: Optional[str] = None
    device_id: Optional[str] = None
    # Xiaohongshu
    xhs_token: Optional[str] = None
    # 百家号
    bduss: Optional[str] = None
    ptoken: Optional[str] = None
    # YouTube
    api_key: Optional[str] = None
    channel_id: Optional[str] = None


class PlatformAccountBase(BaseModel):
    platform_type: PlatformType = Field(..., description="平台类型")
    account_name: str = Field(..., max_length=128, description="账号名称")
    crawl_cron: str = Field("08:00", max_length=32, description="爬虫cron表达式")
    push_cron: str = Field("09:00", max_length=32, description="推送cron表达式")
    config: Dict[str, Any] = Field(default_factory=dict, description="平台特定配置")


class PlatformAccountCreate(PlatformAccountBase):
    credentials: AccountCredentials = Field(..., description="账号凭证")


class PlatformAccountUpdate(BaseModel):
    account_name: Optional[str] = Field(None, max_length=128)
    credentials: Optional[AccountCredentials] = None
    crawl_cron: Optional[str] = Field(None, max_length=32)
    push_cron: Optional[str] = Field(None, max_length=32)
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class PlatformAccountResponse(PlatformAccountBase):
    id: int
    account_id: str
    platform_id: int
    credentials_masked: Dict[str, Any] = Field(default_factory=dict)
    is_deleted: bool
    deleted_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: _serialize_datetime},
    )

    @model_validator(mode='before')
    @classmethod
    def extract_platform_type(cls, data):
        if hasattr(data, '__dict__'):
            # SQLAlchemy model - extract platform_type from relationship
            obj = data
            result = {}
            for field in cls.model_fields:
                if hasattr(obj, field):
                    value = getattr(obj, field)
                    if field == 'account_id' and hasattr(value, '__str__'):
                        value = str(value)
                    result[field] = value
            # platform_type 需要从 platform 关系中获取
            if hasattr(obj, 'platform') and obj.platform:
                result['platform_type'] = obj.platform.platform_type
            return result
        return data


class PlatformAccountListResponse(BaseModel):
    items: List[PlatformAccountResponse]
    total: int


class PlatformAccountScheduleUpdate(BaseModel):
    """更新账号调度时间"""
    crawl_cron: Optional[str] = Field(None, max_length=32)
    push_cron: Optional[str] = Field(None, max_length=32)


class PlatformAccountRestoreResponse(BaseModel):
    """恢复账号响应"""
    id: int
    account_id: str
    account_name: str
    is_deleted: bool
    is_active: bool
    message: str = "账号已恢复"
