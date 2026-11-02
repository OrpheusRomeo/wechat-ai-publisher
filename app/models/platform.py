# app/models/platform.py
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean,
    ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
import uuid
from app.core.database import Base


class PlatformType(str, enum.Enum):
    WECHAT = "wechat"
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BAIJIAHAO = "baijiahao"
    TOUTIAO = "toutiao"
    YOUTUBE = "youtube"


class Platform(Base):
    """平台定义表（如微信公众号、抖音、小红书等）"""
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform_type = Column(String(32), unique=True, nullable=False, index=True)
    display_name = Column(String(64), nullable=False)
    icon = Column(String(128), default="")
    is_active = Column(Boolean, default=True, server_default="true")
    created_at = Column(DateTime, server_default=func.now())

    # 关联账号
    accounts = relationship("PlatformAccount", back_populates="platform")

    __table_args__ = (
        Index('ix_platforms_type_active', 'platform_type', 'is_active'),
    )


class PlatformAccount(Base):
    """平台账号表 - 支持多账号多平台"""
    __tablename__ = "platform_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False, index=True)
    platform_id = Column(Integer, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    account_name = Column(String(128), nullable=False)

    # Fernet 加密的凭证 JSON（平台特定）
    credentials_encrypted = Column(Text, nullable=False, default="")
    # 显示用的掩码（如 {"app_id": "wx12****cdef"}）
    credentials_masked = Column(JSONB, default=dict)

    # Per-account cron 调度（字符串格式 "HH:MM" 或完整 cron 表达式）
    crawl_cron = Column(String(32), default="08:00")
    push_cron = Column(String(32), default="09:00")

    # 平台特定配置（每日配额、开关等）
    config = Column(JSONB, default=dict)

    # 软删除
    is_deleted = Column(Boolean, default=False, server_default="false", nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 关联
    platform = relationship("Platform", back_populates="accounts")
    # 反向引用（通过外键）
    hot_topics = relationship("HotTopic", back_populates="platform_account")
    articles = relationship("GeneratedArticle", back_populates="platform_account")

    __table_args__ = (
        Index('ix_platform_accounts_platform_deleted', 'platform_id', 'is_deleted'),
        Index('ix_platform_accounts_active', 'is_active', 'is_deleted'),
    )
