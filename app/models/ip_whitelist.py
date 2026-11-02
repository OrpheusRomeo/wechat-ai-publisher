# app/models/ip_whitelist.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, text, ForeignKey, Index
from sqlalchemy.sql import func
from app.core.database import Base


class IPWhitelist(Base):
    __tablename__ = "ip_whitelist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip_or_cidr = Column(String(64), nullable=False, unique=True, index=True)
    # 修复 L13: server_default 让 DDL 与已有 DB 现状一致, alembic check 不再误报漂移
    label = Column(String(128), default="", server_default=text("''"), nullable=False)
    enabled = Column(Boolean, default=True, server_default=text("true"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    created_by = Column(String(64), default="", server_default=text("''"), nullable=False)

    # 所属平台账号（NULL 表示全局白名单）
    # 注意：列类型为 Integer，引用 platform_accounts.id (PK)
    account_id = Column(
        Integer,
        ForeignKey("platform_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index('ix_ip_whitelist_account_enabled', 'account_id', 'enabled'),
    )
