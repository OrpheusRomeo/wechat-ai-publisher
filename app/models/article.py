# app/models/article.py
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Enum, ForeignKey,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid
from app.core.database import Base

class ArticleStatus(str, enum.Enum):
    GENERATING = "generating"
    GENERATED = "generated"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    DRAFT = "draft"
    FAILED = "failed"

class GeneratedArticle(Base):
    __tablename__ = 'generated_articles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)
    # FK 到 hot_topics.id，删除话题时级联清理文章；与 UniqueConstraint 联合
    # 防止 Celery 与 Kafka 双路径对同一 (topic, choice) 重复生成 (#4)
    # 关键词深度文章 (keyword-based) 不关联话题，此处可为 NULL
    topic_id = Column(
        Integer,
        ForeignKey("hot_topics.id", ondelete="CASCADE"),
        nullable=True,
    )
    # choice_id 是 user_choices.choice_id 的逻辑外键，但不强加 DB FK
    # （保留灵活性，且配合唯一约束做幂等）
    choice_id = Column(UUID(as_uuid=True), nullable=True)
    # 发布成功后定位推送用户
    user_openid = Column(String(100), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text)
    cover_url = Column(String(500))
    tags = Column(JSONB, default=list)
    status = Column(Enum(ArticleStatus), default=ArticleStatus.GENERATING)
    wechat_media_id = Column(String(100))
    wechat_publish_id = Column(String(100))
    error_msg = Column(Text)
    extra_metadata = Column("metadata", JSONB, default=dict)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())
    publish_time = Column(DateTime)

    # 所属平台账号（NULL 表示全局/legacy 数据）
    platform_account_id = Column(
        Integer,
        ForeignKey("platform_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 平台媒体 ID（统一字段，替换 wechat_media_id/wechat_publish_id）
    platform_media_id = Column(String(256), nullable=True)

    # 关联
    platform_account = relationship("PlatformAccount", back_populates="articles")

    __table_args__ = (
        Index('ix_articles_status_create', 'status', 'create_time'),
        Index('ix_articles_wechat_media', 'wechat_media_id'),
        Index('ix_articles_choice_id', 'choice_id'),
        Index('ix_articles_platform_account', 'platform_account_id', 'status'),
        # NULL choice_id 不参与唯一约束（PG 行为），未走 UserChoice 流程的文章仍可入库
        UniqueConstraint('topic_id', 'choice_id', name='uq_articles_topic_choice'),
    )

