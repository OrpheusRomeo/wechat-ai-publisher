# app/schemas/topic.py
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum

from app.schemas.common import _serialize_datetime

class TopicStatus(str, Enum):
    """话题状态枚举"""
    PENDING = "pending"
    PUSHED = "pushed"
    SELECTED = "selected"
    PROCESSED = "processed"
    FAILED = "failed"

class TopicBase(BaseModel):
    """话题基础模型"""
    title: str = Field(..., min_length=1, max_length=200, description="话题标题")
    heat_score: float = Field(default=0.0, ge=0, description="热度分数")
    source_url: Optional[str] = Field(default=None, max_length=500, description="来源URL")
    summary: Optional[str] = Field(default=None, description="话题摘要")
    category: Optional[str] = Field(default=None, max_length=50, description="分类")
    keywords: List[str] = Field(default=[], description="关键词列表")
    extra_metadata: Dict[str, Any] = Field(default={}, description="元数据")

class TopicCreate(TopicBase):
    """创建话题请求"""
    pass

class TopicUpdate(BaseModel):
    """更新话题请求"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    heat_score: Optional[float] = Field(default=None, ge=0)
    summary: Optional[str] = Field(default=None)
    category: Optional[str] = Field(default=None, max_length=50)
    status: Optional[TopicStatus] = Field(default=None)
    extra_metadata: Optional[Dict[str, Any]] = Field(default=None)

class TopicStatusUpdate(BaseModel):
    """更新话题状态请求"""
    status: TopicStatus = Field(..., description="新状态")
    reason: Optional[str] = Field(default=None, description="状态变更原因")

class TopicResponse(TopicBase):
    """话题响应模型"""
    id: int = Field(..., description="话题ID")
    topic_id: UUID = Field(..., description="话题唯一标识")
    status: TopicStatus = Field(..., description="话题状态")
    create_time: datetime = Field(..., description="创建时间")
    update_time: datetime = Field(..., description="更新时间")
    push_time: Optional[datetime] = Field(default=None, description="推送时间")
    
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: _serialize_datetime},
    )

class TopicListResponse(BaseModel):
    """话题列表响应"""
    items: List[TopicResponse] = Field(..., description="话题列表")
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")

class TopicPushRequest(BaseModel):
    """推送话题请求"""
    topic_ids: List[int] = Field(..., min_length=1, description="话题ID列表")
    user_openids: Optional[List[str]] = Field(default=None, description="指定用户列表")

class TopicChoiceRequest(BaseModel):
    """用户选择话题请求"""
    topic_id: int = Field(..., description="话题ID")
    choice_index: int = Field(..., ge=1, description="选择序号")
    user_openid: str = Field(..., description="用户OpenID")

class BatchDeleteTopicsRequest(BaseModel):
    """批量删除话题请求"""
    ids: List[int] = Field(..., min_length=1, max_length=500, description="要删除的话题 id 列表")

