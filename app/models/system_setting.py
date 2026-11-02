# app/models/system_setting.py
"""系统配置表：存储可在线修改的配置项。

敏感字段（如 API Key/AppSecret）用 AES-GCM 加密存储。
非敏感值（如定时时间）明文存储，方便直接查询。
"""
from sqlalchemy import Column, String, Text, Boolean, DateTime, text
from sqlalchemy.sql import func
from app.core.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    # 配置键，如 "wechat_app_id", "deepseek_api_key", "scheduler_crawl_time"
    key = Column(String(128), primary_key=True, nullable=False)
    # 加密后的值（敏感字段用 Fernet 加密，非敏感字段存明文)
    # 修复 L13: server_default 让 DDL 与已有 DB 现状一致, alembic check 不再误报漂移
    value_encrypted = Column(Text, default="", server_default=text("''"), nullable=False)
    # 掩码显示值（如 "wx12****cdef"），用于前端展示
    value_masked = Column(String(128), default="", server_default=text("''"), nullable=False)
    # 分类: wechat / deepseek / scheduler / system
    category = Column(String(32), default="system", server_default=text("'system'"), nullable=False, index=True)
    # 字段描述
    description = Column(String(256), default="", server_default=text("''"), nullable=False)
    # 是否为敏感字段（敏感字段在前端显示掩码，且需要再次确认才能修改）
    is_secret = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    # 前端输入提示
    placeholder = Column(String(256), default="", server_default=text("''"), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(64), default="", server_default=text("''"), nullable=False)
