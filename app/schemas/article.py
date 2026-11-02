# app/schemas/article.py
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum

from app.schemas.common import _serialize_datetime

class ArticleStatus(str, Enum):
    """文章状态枚举"""
    GENERATING = "generating"
    GENERATED = "generated"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    DRAFT = "draft"
    FAILED = "failed"

class ArticleBase(BaseModel):
    """文章基础模型"""
    title: Optional[str] = Field(default=None, max_length=200, description="文章标题")
    content: Optional[str] = Field(default=None, description="文章内容")
    summary: Optional[str] = Field(default=None, description="文章摘要")
    cover_url: Optional[str] = Field(default=None, max_length=500, description="封面图URL")
    tags: List[str] = Field(default=[], description="标签列表")
    extra_metadata: Dict[str, Any] = Field(default={}, description="元数据")
    
    @field_validator('summary')
    @classmethod
    def validate_summary(cls, v, info):
        """自动生成摘要。

        注意: pydantic v2 与 v1 一致, 字段缺省（有默认值）时不触发校验器,
        所以只在调用方显式传了 summary（哪怕是空串）时才会自动补全。
        """
        if not v:
            content = (info.data or {}).get('content') or ''
            if content:
                # 去除Markdown标记，取前200字符
                import re
                clean_content = re.sub(r'[#*_`>]', '', content)
                v = clean_content[:200] + '...' if len(clean_content) > 200 else clean_content
        return v

class ArticleCreate(ArticleBase):
    """创建文章请求"""
    topic_id: int = Field(..., description="关联话题ID")

class ArticleUpdate(BaseModel):
    """更新文章请求"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1)
    summary: Optional[str] = Field(default=None)
    cover_url: Optional[str] = Field(default=None, max_length=500)
    tags: Optional[List[str]] = Field(default=None)
    status: Optional[ArticleStatus] = Field(default=None)
    extra_metadata: Optional[Dict[str, Any]] = Field(default=None)

class ArticleStatusUpdate(BaseModel):
    """更新文章状态请求"""
    status: ArticleStatus = Field(..., description="新状态")
    error_msg: Optional[str] = Field(default=None, description="错误信息")

class ArticlePublishRequest(BaseModel):
    """发布文章请求"""
    article_id: int = Field(..., description="文章ID")
    publish_type: str = Field(default="auto", description="发布类型: auto/manual")
    scheduled_time: Optional[datetime] = Field(default=None, description="定时发布时间")

class ArticleResponse(ArticleBase):
    """文章响应模型"""
    id: int = Field(..., description="文章ID")
    article_id: UUID = Field(..., description="文章唯一标识")
    topic_id: Optional[int] = Field(default=None, description="关联话题ID（关键词深度文章为 NULL）")
    choice_id: Optional[UUID] = Field(default=None, description="触发生成的 UserChoice.choice_id, 用于幂等去重")
    user_openid: Optional[str] = Field(default=None, max_length=100, description="触发用户的 openid")
    status: ArticleStatus = Field(..., description="文章状态")
    wechat_media_id: Optional[str] = Field(default=None, description="微信草稿ID")
    wechat_publish_id: Optional[str] = Field(default=None, description="微信发布ID")
    platform_account_id: Optional[int] = Field(default=None, description="发布到的平台账号ID")
    platform_media_id: Optional[str] = Field(default=None, description="平台媒体ID（统一字段）")
    error_msg: Optional[str] = Field(default=None, description="错误信息")
    create_time: datetime = Field(..., description="创建时间")
    update_time: datetime = Field(..., description="更新时间")
    publish_time: Optional[datetime] = Field(default=None, description="发布时间")

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: _serialize_datetime},
    )

class ArticleListResponse(BaseModel):
    """文章列表响应"""
    items: List[ArticleResponse] = Field(..., description="文章列表")
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")

class ArticleGenerateRequest(BaseModel):
    """生成文章请求"""
    topic_id: int = Field(..., description="话题ID")
    choice_id: Optional[str] = Field(default=None, description="选择ID")
    user_openid: Optional[str] = Field(default=None, max_length=100, description="触发用户的 openid, 发布成功后用于推送通知")
    custom_prompt: Optional[str] = Field(default=None, description="自定义提示词")
    llm_provider_id: Optional[int] = Field(default=None, description="指定 LLM 供应商 ID，不指定则使用默认供应商")

class ArticlePreviewRequest(BaseModel):
    """文章预览请求"""
    topic_title: str = Field(..., min_length=1, max_length=200, description="话题标题")
    style: str = Field(default="standard", description="文章风格: standard/detailed/brief")
    llm_provider_id: Optional[int] = Field(default=None, description="指定 LLM 供应商 ID，不指定则使用默认供应商")

class BatchDeleteArticlesRequest(BaseModel):
    """批量删除文章请求"""
    ids: List[int] = Field(..., min_length=1, max_length=500, description="要删除的文章 id 列表")

class ArticlePublishToPlatformRequest(BaseModel):
    """发布文章到指定平台账号"""
    platform_account_id: int = Field(..., description="目标平台账号 ID")
