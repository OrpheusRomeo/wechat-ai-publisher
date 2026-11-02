# app/models/platform_ip_whitelist.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, func, UniqueConstraint, Index
from app.core.database import Base


class PlatformIPWhitelist(Base):
    """各平台（微信/抖音/今日头条等）对外调用的出站 IP 白名单。

    与 app.models.ip_whitelist.IPWhitelist（管理后台访问控制）是两张完全独立的表，
    用途完全不同：前者是"别人访问我"，后者是"我访问别人"。
    """

    __tablename__ = "platform_ip_whitelist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 平台类型，对应 PlatformType 枚举
    platform_type = Column(String(32), nullable=False, index=True)
    # 公网出口 IP
    ip_address = Column(String(64), nullable=False)
    # 备注
    label = Column(String(128), default="", nullable=False)
    # 是否启用
    is_active = Column(Boolean, default=True, nullable=False)
    # 是否为系统自动同步的 IP
    is_auto_synced = Column(Boolean, default=False, nullable=False)
    # 创建/更新时间
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("platform_type", "ip_address", name="uq_platform_ip"),
        Index("ix_piw_platform_active", "platform_type", "is_active"),
    )
