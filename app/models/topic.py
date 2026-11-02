# app/models/topic.py
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Enum, Float, ForeignKey,
    Boolean, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid
from app.core.database import Base

class TopicStatus(str, enum.Enum):
    PENDING = "pending"
    PUSHED = "pushed"
    SELECTED = "selected"
    PROCESSED = "processed"
    FAILED = "failed"

class HotTopic(Base):
    __tablename__ = 'hot_topics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)
    title = Column(String(200), nullable=False)
    heat_score = Column(Float, nullable=False)
    source_url = Column(String(500))
    summary = Column(Text)
    category = Column(String(50))
    keywords = Column(JSONB, default=list)
    status = Column(Enum(TopicStatus), default=TopicStatus.PENDING)
    extra_metadata = Column("metadata", JSONB, default=dict)
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())
    push_time = Column(DateTime)

    # 所属平台账号（NULL 表示全局/legacy 数据）
    platform_account_id = Column(
        Integer,
        ForeignKey("platform_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 关联
    platform_account = relationship("PlatformAccount", back_populates="hot_topics")

    __table_args__ = (
        Index('ix_hot_topics_status_create', 'status', 'create_time'),
        Index('ix_hot_topics_heat', 'heat_score'),
        Index('ix_hot_topics_account_status', 'platform_account_id', 'status'),
        Index('ix_hot_topics_account_id', 'platform_account_id'),
    )

class UserChoice(Base):
    __tablename__ = 'user_choices'

    id = Column(Integer, primary_key=True, autoincrement=True)
    choice_id = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)
    user_openid = Column(String(100), nullable=False)
    # FK 到 hot_topics.id；删话题时级联清理用户选择 (#15)
    topic_id = Column(
        Integer,
        ForeignKey("hot_topics.id", ondelete="CASCADE"),
        nullable=False,
    )
    choice_index = Column(Integer)
    choose_time = Column(DateTime, server_default=func.now())
    is_processed = Column(Boolean, default=False)
    process_time = Column(DateTime)

    __table_args__ = (
        Index('ix_user_choices_user_processed', 'user_openid', 'is_processed'),
        Index('ix_user_choices_topic', 'topic_id'),
        Index('ix_user_choices_is_processed', 'is_processed'),
        # 修复 M1: 同一 (openid, topic) 不能重复选择, 在 DB 层强制唯一,
        # 防止 POST /choice 的 check-then-insert TOCTOU 竞态
        UniqueConstraint('user_openid', 'topic_id', name='uq_user_choice_openid_topic'),
    )

