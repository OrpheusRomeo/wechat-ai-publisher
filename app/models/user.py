# app/models/user.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class UserSubscription(Base):
    """用户订阅/绑定关系：openid 与服务内用户对应。"""
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False)
    # 修复 L13: 用显式 UniqueConstraint 替代 column-level unique=True,
    # alembic 对 column-level unique 的探测不可靠, 显式声明更稳定.
    openid = Column(String(100), nullable=False)
    unionid = Column(String(100), index=True)
    nickname = Column(String(100))
    is_active = Column(Boolean, default=True)
    subscribe_time = Column(DateTime, server_default=func.now())
    last_interact_time = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # 显式约束名: 与 DB 里 user_subscriptions_openid_key 对齐
        UniqueConstraint("openid", name="user_subscriptions_openid_key"),
        # user_id 的 unique constraint 也显式声明
        UniqueConstraint("user_id", name="user_subscriptions_user_id_key"),
        # 索引 (openid)
        Index("ix_user_subscriptions_openid", "openid", unique=True),
        Index("ix_user_subs_active_last", "is_active", "last_interact_time"),
    )
