# app/api/v1/articles.py
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from app.core.auth import require_admin_auth, require_ip_whitelist  # #3
from app.core.database import get_db
from app.core.rate_limit import limiter  # #12 全局限流器
from app.models.article import GeneratedArticle, ArticleStatus
from app.schemas.article import (
    ArticleResponse, ArticleCreate, ArticleUpdate, ArticleStatusUpdate,
    ArticleListResponse, ArticlePublishRequest, ArticleGenerateRequest,
    ArticlePreviewRequest,
    BatchDeleteArticlesRequest,
    ArticlePublishToPlatformRequest,
)
from app.schemas.common import DataResponse
from app.tasks.article_tasks import generate_article, publish_article

logger = logging.getLogger(__name__)

# 整个 articles router 强制要求鉴权 + IP 白名单
router = APIRouter(dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)])

@router.get("/", response_model=ArticleListResponse)
@limiter.limit("60/minute")  # 修复 L2: 列表端点也加限流, 防运营脚本打爆
async def get_articles(
    request: Request,
    status: Optional[ArticleStatus] = None,
    topic_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """获取文章列表"""
    query = db.query(GeneratedArticle)

    if status:
        query = query.filter(GeneratedArticle.status == status)
    if topic_id:
        query = query.filter(GeneratedArticle.topic_id == topic_id)

    total = query.count()
    articles = query.order_by(
        GeneratedArticle.create_time.desc()
    ).offset((page - 1) * page_size).limit(page_size).all()

    return ArticleListResponse(
        items=[ArticleResponse.model_validate(a) for a in articles],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size
    )

@router.get("/{article_id}", response_model=DataResponse[ArticleResponse])
async def get_article(
    article_id: int,
    db: Session = Depends(get_db)
):
    """获取文章详情"""
    article = db.query(GeneratedArticle).filter(
        GeneratedArticle.id == article_id
    ).first()
    
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    
    return DataResponse(data=ArticleResponse.model_validate(article))

@router.post("/", response_model=DataResponse[ArticleResponse])
async def create_article(
    article_data: ArticleCreate,
    db: Session = Depends(get_db)
):
    """创建文章"""
    article = GeneratedArticle(**article_data.model_dump())
    db.add(article)
    db.commit()
    db.refresh(article)
    
    return DataResponse(
        code=0,
        message="创建成功",
        data=ArticleResponse.model_validate(article)
    )

@router.put("/{article_id}", response_model=DataResponse[ArticleResponse])
async def update_article(
    article_id: int,
    article_data: ArticleUpdate,
    db: Session = Depends(get_db)
):
    """更新文章.

    修复 L10: 仅 ``exclude_unset=True`` 的字段被 set; 显式传 ``None`` 也可清空字段
    (之前 ``if value is not None`` 会吞掉显式 ``null``).
    """
    article = db.query(GeneratedArticle).filter(
        GeneratedArticle.id == article_id
    ).first()

    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    for key, value in article_data.model_dump(exclude_unset=True).items():
        setattr(article, key, value)

    db.commit()
    db.refresh(article)

    return DataResponse(
        code=0,
        message="更新成功",
        data=ArticleResponse.model_validate(article)
    )

@router.post("/generate", response_model=DataResponse)
@limiter.limit("10/minute")  # #12 DeepSeek 限流
async def generate_article_from_topic(
    request: Request,
    req: ArticleGenerateRequest,
    db: Session = Depends(get_db),
):
    """生成文章。

    修复 M9: 入口先查 topic 是否存在, 不存在直接 404 而不是 200 + Celery 内部失败.
    修复 L6: ``custom_prompt`` 透传到 Celery 任务, 后端 ``DeepSeekService.generate_article`` 已接收.

    注意: slowapi 按形参名 ``request`` 找 Starlette Request, body 形参必须改名.
    """
    from app.models.topic import HotTopic
    topic = db.query(HotTopic).filter(HotTopic.id == req.topic_id).first()
    if topic is None:
        raise HTTPException(
            status_code=404, detail=f"话题不存在: {req.topic_id}"
        )

    # 异步生成
    task = generate_article.delay(
        req.topic_id,
        req.choice_id or "",
        req.user_openid or "",
        req.custom_prompt or "",
        req.llm_provider_id,
    )

    return DataResponse(
        code=0,
        message="文章生成任务已启动",
        data={"task_id": task.id}
    )

@router.post("/preview", response_model=DataResponse)
@limiter.limit("5/minute")  # #12 LLM 同步调用, 限流更严
async def preview_article(
    request: Request,
    req: ArticlePreviewRequest,
    db: Session = Depends(get_db),
):
    """预览文章 (不保存)。使用 LLM 供应商系统生成预览.

    注意: slowapi 按形参名 ``request`` 找 Starlette Request, body 形参必须改名.
    """
    from app.services.llm_provider import LLMProviderService
    from app.services.llm_clients import build_llm_client

    svc = LLMProviderService(db)
    if req.llm_provider_id:
        provider = svc.get_provider(req.llm_provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail="LLM 供应商不存在")
    else:
        provider = svc.get_default_provider()
        if not provider:
            raise HTTPException(status_code=400, detail="未配置默认 LLM 供应商")

    api_key = svc.decrypt_api_key(provider.id)
    client = build_llm_client(
        provider_type=provider.provider_type,
        api_key=api_key or provider.api_key_masked,
        api_url=provider.api_url,
        model=provider.model,
        temperature=provider.temperature,
        timeout=provider.timeout,
        extra_config=provider.extra_config or {},
    )

    style_hint = (
        "格式: 详细 1500-2000 字"
        if req.style == "detailed"
        else "格式: 简短 600 字以内"
        if req.style == "brief"
        else "格式: 标准 1500 字"
    )
    custom_prompt = f"{style_hint}\n话题: {req.topic_title}"
    result = client.generate_article(
        req.topic_title, custom_prompt=custom_prompt,
    )

    if not result:
        raise HTTPException(status_code=500, detail="文章生成失败")

    return DataResponse(
        code=0,
        message="预览生成成功",
        data=result
    )

@router.post("/{article_id}/publish", response_model=DataResponse)
async def publish_article_endpoint(
    article_id: int,
    publish_type: str = "auto",
    scheduled_time: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """发布文章"""
    article = db.query(GeneratedArticle).filter(
        GeneratedArticle.id == article_id
    ).first()

    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    if article.status == ArticleStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="文章已发布")

    # 异步发布
    task = publish_article.delay(article_id)

    return DataResponse(
        code=0,
        message="发布任务已启动",
        data={"task_id": task.id}
    )

@router.post("/{article_id}/publish-to-platform", response_model=DataResponse)
async def publish_article_to_platform(
    article_id: int,
    req: ArticlePublishToPlatformRequest,
    db: Session = Depends(get_db),
):
    """将文章发布到指定平台（头条、小红书等），支持多账号。

    根据 platform_account_id 找到对应平台账号，通过 Kafka 异步发布，
    由 toutiao_publish_consumer 消费并执行实际 API 调用。
    成功后回写 platform_media_id。

    支持多账号：配置多个头条/小红书账号，调用时传入不同的 platform_account_id
    即可实现"不同账号发不同文章"。
    """
    article = db.query(GeneratedArticle).filter(
        GeneratedArticle.id == article_id
    ).first()

    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    if article.status == ArticleStatus.PUBLISHED:
        raise HTTPException(
            status_code=400,
            detail="文章已发布，如需重新发布请先更改状态",
        )

    # 投递到 Kafka，由后台消费者执行发布
    try:
        from app.services.kafka_producer import KafkaProducerService
        KafkaProducerService().send_publish_to_platform_request(
            article_id=article_id,
            platform_account_id=req.platform_account_id,
        )
        logger.info(
            "多平台发布请求已投递 Kafka: article_id=%s platform_account_id=%s",
            article_id, req.platform_account_id,
        )
    except Exception as exc:
        logger.error("投递 Kafka 失败: %s", exc)
        raise HTTPException(status_code=500, detail="消息投递失败，请重试")

    return DataResponse(
        code=0,
        message="发布任务已投递给后台处理",
        data={
            "article_id": article_id,
            "platform_account_id": req.platform_account_id,
        },
    )

@router.patch("/{article_id}/status", response_model=DataResponse[ArticleResponse])
async def update_article_status(
    article_id: int,
    status_update: ArticleStatusUpdate,
    db: Session = Depends(get_db)
):
    """更新文章状态"""
    article = db.query(GeneratedArticle).filter(
        GeneratedArticle.id == article_id
    ).first()
    
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    
    article.status = status_update.status
    if status_update.error_msg:
        article.error_msg = status_update.error_msg
    
    db.commit()
    db.refresh(article)

    return DataResponse(
        code=0,
        message="状态更新成功",
        data=ArticleResponse.model_validate(article)
    )


@router.post("/batch_delete", response_model=DataResponse)
async def batch_delete_articles(
    req: BatchDeleteArticlesRequest,
    db: Session = Depends(get_db),
):
    """批量删除文章.

    物理删除数据库记录; 微信侧 media_id / publish_id 不会联动删除,
    草稿箱 / 已发布文章会留在公众号后台, 由运营手工清理.
    """
    deleted = (
        db.query(GeneratedArticle)
        .filter(GeneratedArticle.id.in_(req.ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("批量删除文章: requested=%d deleted=%d", len(req.ids), deleted)
    return DataResponse(
        code=0,
        message=f"已删除 {deleted} 篇文章",
        data={"requested": len(req.ids), "deleted": deleted},
    )

