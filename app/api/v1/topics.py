# app/api/v1/topics.py
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import timedelta
from app.core.config import today_start

from app.core.auth import require_admin_auth, require_ip_whitelist  # #3
from app.core.database import get_db
from app.core.rate_limit import limiter  # 修复 L2
from app.models.topic import HotTopic, TopicStatus, UserChoice
from app.schemas.topic import (
    TopicResponse, TopicCreate, TopicUpdate, TopicStatusUpdate,
    TopicListResponse, TopicPushRequest, TopicChoiceRequest,
    TopicStatus as TopicStatusEnum,
    BatchDeleteTopicsRequest,
)
from app.schemas.common import DataResponse, ListResponse
from app.tasks.crawl_tasks import crawl_hot_topics, crawl_for_account
from app.tasks.push_tasks import push_topics_to_users

logger = logging.getLogger(__name__)
from sqlalchemy.exc import IntegrityError

# 整个 topics router 强制要求鉴权 + IP 白名单
router = APIRouter(dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)])

@router.get("/", response_model=TopicListResponse)
@limiter.limit("60/minute")  # 修复 L2
async def get_topics(
    request: Request,
    status: Optional[TopicStatusEnum] = None,
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """获取话题列表.

    修复 L9: 按 title 去重, 每个 title 只返回最新入库的那一条.
    用 ``max(id) group by title`` 子查询做兜底, 即便历史脏数据残留
    也能在前端展示干净.
    """
    from sqlalchemy import func

    # 子查询: 每个 title 取 max(id) — 最新入库的那条
    max_id_subq = (
        db.query(func.max(HotTopic.id).label("max_id"))
        .group_by(HotTopic.title)
        .subquery()
    )
    query = db.query(HotTopic).join(
        max_id_subq, HotTopic.id == max_id_subq.c.max_id,
    )

    if status:
        query = query.filter(HotTopic.status == status)
    if category:
        query = query.filter(HotTopic.category == category)

    total = query.count()
    topics = query.order_by(HotTopic.create_time.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return TopicListResponse(
        items=[TopicResponse.model_validate(t) for t in topics],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size
    )

@router.get("/today", response_model=ListResponse[TopicResponse])
async def get_today_topics(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """获取今日热点话题 (按 title 去重)"""
    from sqlalchemy import func
    today_start = today_start()
    max_id_subq = (
        db.query(func.max(HotTopic.id).label("max_id"))
        .filter(HotTopic.create_time >= today_start)
        .group_by(HotTopic.title)
        .subquery()
    )
    topics = (
        db.query(HotTopic)
        .join(max_id_subq, HotTopic.id == max_id_subq.c.max_id)
        .filter(HotTopic.status == TopicStatus.PENDING)
        .order_by(HotTopic.create_time.desc())
        .limit(limit)
        .all()
    )

    return ListResponse(
        data=[TopicResponse.model_validate(t) for t in topics],
        total=len(topics)
    )

@router.get("/{topic_id}", response_model=DataResponse[TopicResponse])
async def get_topic(
    topic_id: int,
    db: Session = Depends(get_db)
):
    """获取话题详情"""
    topic = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="话题不存在")
    
    return DataResponse(data=TopicResponse.model_validate(topic))

@router.post("/", response_model=DataResponse[TopicResponse])
async def create_topic(
    topic_data: TopicCreate,
    db: Session = Depends(get_db)
):
    """创建话题"""
    topic = HotTopic(**topic_data.model_dump())
    db.add(topic)
    db.commit()
    db.refresh(topic)
    
    return DataResponse(
        code=0,
        message="创建成功",
        data=TopicResponse.model_validate(topic)
    )

@router.put("/{topic_id}", response_model=DataResponse[TopicResponse])
async def update_topic(
    topic_id: int,
    topic_data: TopicUpdate,
    db: Session = Depends(get_db)
):
    """更新话题"""
    topic = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="话题不存在")
    
    for key, value in topic_data.model_dump(exclude_unset=True).items():
        setattr(topic, key, value)
    
    db.commit()
    db.refresh(topic)
    
    return DataResponse(
        code=0,
        message="更新成功",
        data=TopicResponse.model_validate(topic)
    )

@router.patch("/{topic_id}/status", response_model=DataResponse[TopicResponse])
async def update_topic_status(
    topic_id: int,
    status_update: TopicStatusUpdate,
    db: Session = Depends(get_db)
):
    """更新话题状态"""
    topic = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="话题不存在")
    
    topic.status = status_update.status
    db.commit()
    db.refresh(topic)
    
    return DataResponse(
        code=0,
        message="状态更新成功",
        data=TopicResponse.model_validate(topic)
    )

@router.post("/crawl", response_model=DataResponse)
async def trigger_crawl():
    """手动触发爬取热点（全局）"""
    result = crawl_hot_topics.delay()
    return DataResponse(
        code=0,
        message="爬取任务已启动",
        data={"task_id": result.id}
    )


@router.post("/crawl/{account_id}", response_model=DataResponse)
async def trigger_crawl_for_account(account_id: str):
    """手动触发为指定账号爬取热点"""
    from uuid import UUID, InvalidUUIDError
    try:
        UUID(account_id)  # 验证 UUID 格式
    except (InvalidUUIDError, ValueError):
        raise HTTPException(status_code=400, detail="无效的账号 ID 格式")
    result = crawl_for_account.delay(account_id)
    return DataResponse(
        code=0,
        message="账号爬取任务已启动",
        data={"task_id": result.id}
    )

@router.post("/push", response_model=DataResponse)
async def trigger_push():
    """手动触发推送话题"""
    result = push_topics_to_users.delay()
    return DataResponse(
        code=0,
        message="推送任务已启动",
        data={"task_id": result.id}
    )

@router.post("/choice", response_model=DataResponse)
async def user_choice(
    choice_data: TopicChoiceRequest,
    db: Session = Depends(get_db)
):
    """用户选择话题。

    修复 M1: 依赖 0003 迁移建立的 ``uq_user_choice_openid_topic`` 唯一约束,
    并发同 (openid, topic) 请求会被 PG 拒绝, 这里捕 ``IntegrityError`` 转 400.
    """
    topic = db.query(HotTopic).filter(HotTopic.id == choice_data.topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="话题不存在")

    choice = UserChoice(
        user_openid=choice_data.user_openid,
        topic_id=choice_data.topic_id,
        choice_index=choice_data.choice_index,
    )
    db.add(choice)
    try:
        db.flush()  # 让 IntegrityError 现在抛出, 不留到 commit
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="您已选择过该话题")

    # 更新话题状态: 仅 PENDING/PUSHED 可推进为 SELECTED, 不覆盖已处理状态
    if topic.status in (TopicStatus.PENDING, TopicStatus.PUSHED):
        topic.status = TopicStatus.SELECTED
    db.commit()
    db.refresh(choice)

    return DataResponse(
        code=0,
        message="选择成功，正在生成文章",
        data={"choice_id": str(choice.choice_id)}
    )


@router.post("/batch_delete", response_model=DataResponse)
async def batch_delete_topics(
    req: BatchDeleteTopicsRequest,
    db: Session = Depends(get_db),
):
    """批量删除热搜话题.

    FK 约束: ``user_choices.topic_id`` -> ``hot_topics.id`` (ondelete CASCADE)
    会级联清理该话题关联的用户选择记录.
    """
    deleted = (
        db.query(HotTopic)
        .filter(HotTopic.id.in_(req.ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("批量删除热搜: requested=%d deleted=%d", len(req.ids), deleted)
    return DataResponse(
        code=0,
        message=f"已删除 {deleted} 条话题",
        data={"requested": len(req.ids), "deleted": deleted},
    )

