# app/schemas/keyword_article.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class KeywordArticleRequest(BaseModel):
    """请求生成关键词深度文章。"""
    keyword: str = Field(..., min_length=1, max_length=200, description="搜索关键词")
    custom_prompt: str = Field(default="", description="自定义 prompt 覆盖系统默认")
    style: str = Field(default="standard", description="文章风格: standard / detailed / brief")
    auto_publish: bool = Field(default=False, description="生成后是否自动发布到草稿箱")
    llm_provider_id: Optional[int] = Field(default=None, description="指定 LLM 供应商 ID，不指定则使用默认供应商")


class KeywordArticleResponse(BaseModel):
    """关键词文章概要。"""
    id: int
    article_id: str
    title: str
    keyword: str
    status: str
    create_time: Optional[datetime] = None
    publish_time: Optional[datetime] = None

    model_config = {"from_attributes": True}


class KeywordArticleDetail(BaseModel):
    id: int
    article_id: str
    title: str
    content: str
    summary: Optional[str] = None
    keyword: str = ""
    status: str
    wechat_media_id: Optional[str] = None
    wechat_publish_id: Optional[str] = None
    error_msg: Optional[str] = None
    extra_metadata: dict = {}
    create_time: Optional[datetime] = None
    publish_time: Optional[datetime] = None

    model_config = {"from_attributes": True}
