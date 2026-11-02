# app/schemas/user.py
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class UserChoiceBase(BaseModel):
    """用户选择基础模型"""
    user_openid: str = Field(..., min_length=1, max_length=100, description="用户OpenID")
    topic_id: int = Field(..., description="话题ID")
    choice_index: int = Field(..., ge=1, description="选择序号")

class UserChoiceCreate(UserChoiceBase):
    """创建用户选择请求"""
    device_info: Optional[Dict[str, Any]] = Field(default=None, description="设备信息")

class UserChoiceResponse(UserChoiceBase):
    """用户选择响应模型"""
    id: int = Field(..., description="选择ID")
    choice_id: UUID = Field(..., description="选择唯一标识")
    is_processed: bool = Field(..., description="是否已处理")
    choose_time: datetime = Field(..., description="选择时间")
    process_time: Optional[datetime] = Field(default=None, description="处理时间")
    device_info: Optional[Dict[str, Any]] = Field(default=None, description="设备信息")
    
    model_config = ConfigDict(from_attributes=True)

class UserChoiceProcessRequest(BaseModel):
    """处理用户选择请求"""
    choice_ids: List[str] = Field(..., min_length=1, description="选择ID列表")
    action: str = Field(default="generate", description="处理动作: generate/skip")

class UserChoiceListResponse(BaseModel):
    """用户选择列表响应"""
    items: List[UserChoiceResponse] = Field(..., description="选择列表")
    total: int = Field(..., description="总数量")

class UserInfoResponse(BaseModel):
    """用户信息响应"""
    openid: str = Field(..., description="用户OpenID")
    nickname: Optional[str] = Field(default=None, description="昵称")
    headimgurl: Optional[str] = Field(default=None, description="头像URL")
    subscribe_time: Optional[datetime] = Field(default=None, description="关注时间")
    last_choice_time: Optional[datetime] = Field(default=None, description="最后选择时间")
    total_choices: int = Field(default=0, description="总选择次数")
    
    model_config = ConfigDict(from_attributes=True)

