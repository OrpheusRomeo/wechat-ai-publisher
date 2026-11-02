# app/api/v1/keyword_articles.py
"""关键词深度文章接口。

POST /keyword-articles: 输入关键词，启动 Celery 任务生成深度分析文章
GET /keyword-articles: 列出所有关键词文章
GET /keyword-articles/{id}: 查看单篇详情
"""
import logging
from typing import Optional, List

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_admin_auth, require_ip_whitelist
from app.core.database import get_db
from app.models.article import GeneratedArticle, ArticleStatus
from app.schemas.keyword_article import (
    KeywordArticleRequest,
    KeywordArticleResponse,
    KeywordArticleDetail,
)
from app.schemas.common import DataResponse
from app.tasks.article_tasks import generate_deep_article

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/keyword-articles",
    tags=["keyword-articles"],
    dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)],
)


def _to_summary(a: GeneratedArticle) -> KeywordArticleResponse:
    keyword = a.extra_metadata.get("keyword", "") if a.extra_metadata else ""
    return KeywordArticleResponse(
        id=a.id,
        article_id=str(a.article_id),
        title=a.title,
        keyword=keyword,
        status=a.status,
        create_time=a.create_time,
        publish_time=a.publish_time,
    )


def _to_detail(a: GeneratedArticle) -> KeywordArticleDetail:
    keyword = a.extra_metadata.get("keyword", "") if a.extra_metadata else ""
    return KeywordArticleDetail(
        id=a.id,
        article_id=str(a.article_id),
        title=a.title,
        content=a.content,
        summary=a.summary,
        keyword=keyword,
        status=a.status,
        wechat_media_id=a.wechat_media_id,
        wechat_publish_id=a.wechat_publish_id,
        error_msg=a.error_msg,
        extra_metadata=a.extra_metadata or {},
        create_time=a.create_time,
        publish_time=a.publish_time,
    )


@router.post("", response_model=DataResponse[dict])
async def create_keyword_article(
    body: KeywordArticleRequest,
    auth: dict = Depends(require_admin_auth),
):
    """创建关键词深度文章生成任务（异步 Celery）。"""
    task = generate_deep_article.delay(
        keyword=body.keyword,
        custom_prompt=body.custom_prompt,
        style=body.style,
        auto_publish=body.auto_publish,
        llm_provider_id=body.llm_provider_id,
    )
    return DataResponse(
        data={"task_id": task.id, "keyword": body.keyword},
        message="文章生成任务已提交",
    )


@router.get("", response_model=DataResponse[List[KeywordArticleResponse]])
async def list_keyword_articles(
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """列出关键词文章（topic_id 为 NULL 且 extra_metadata.keyword 不为空的文章）。"""
    query = db.query(GeneratedArticle).filter(
        GeneratedArticle.topic_id.is_(None),
        GeneratedArticle.extra_metadata["keyword"].isnot(None),
    )
    if status:
        query = query.filter(GeneratedArticle.status == status)
    query = query.order_by(GeneratedArticle.create_time.desc())
    total = query.count()
    articles = query.offset((page - 1) * page_size).limit(page_size).all()
    return DataResponse(
        data=[_to_summary(a) for a in articles],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get("/{article_id}", response_model=DataResponse[KeywordArticleDetail])
async def get_keyword_article(
    article_id: int,
    db: Session = Depends(get_db),
):
    """获取关键词文章详情。"""
    article = db.query(GeneratedArticle).filter(GeneratedArticle.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return DataResponse(data=_to_detail(article))


class BatchDeleteRequest(BaseModel):
    ids: List[int]


@router.post("/batch_delete", response_model=DataResponse)
async def batch_delete_keyword_articles(
    req: BatchDeleteRequest,
    db: Session = Depends(get_db),
):
    """批量删除关键词文章（物理删除）。

    微信侧 media_id / publish_id 不会联动删除。
    """
    deleted = (
        db.query(GeneratedArticle)
        .filter(
            GeneratedArticle.id.in_(req.ids),
            GeneratedArticle.topic_id.is_(None),  # 关键词文章
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("批量删除关键词文章: requested=%d deleted=%d", len(req.ids), deleted)
    return DataResponse(
        code=0,
        message=f"已删除 {deleted} 篇关键词文章",
        data={"requested": len(req.ids), "deleted": deleted},
    )
