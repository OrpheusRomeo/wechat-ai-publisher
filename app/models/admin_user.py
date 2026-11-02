# app/models/admin_user.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, text
from sqlalchemy.sql import func
from app.core.database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    # 修复 L13: server_default 让 DDL 与已有 DB 现状一致, alembic check 不再误报漂移
    role = Column(
        String(32), default="admin", server_default=text("'admin'"),
        nullable=False,
    )  # superadmin / admin
    nickname = Column(
        String(64), default="", server_default=text("''"),
        nullable=False,
    )
    is_active = Column(
        Boolean, default=True, server_default=text("true"),
        nullable=False,
    )
    created_at = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)
