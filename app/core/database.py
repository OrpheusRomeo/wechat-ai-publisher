# app/core/database.py
"""SQLAlchemy 引擎与 Session 工厂。

- ``get_db``        : FastAPI ``Depends`` 使用的迭代器形式
- ``session_scope`` : Celery 任务 / 脚本使用的上下文管理器
- ``init_db``       : 启动时幂等地创建所有表 (``checkfirst=True``)
"""
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
# 修复 M10: SQLAlchemy 2.0 推荐从 sqlalchemy.orm import declarative_base,
# sqlalchemy.ext.declarative 仍能用但有 deprecation warning
from sqlalchemy.orm import declarative_base, Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    return (
        f"postgresql://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )


engine = create_engine(
    get_db_url(),
    poolclass=QueuePool,
    pool_size=settings.pg_pool_size,
    max_overflow=settings.pg_max_overflow,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.debug,
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "options": f"-c timezone={settings.timezone}",
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：``Depends(get_db)`` 直接拿 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Celery 任务 / 一次性脚本使用，自动 commit/rollback。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """DEPRECATED: 已迁移至 Alembic。保留空实现以兼容旧 ``init_db()`` 调用。

    生产/部署请运行 ``alembic upgrade head``。
    """
    logger.warning(
        "init_db() 已废弃，请改用 `alembic upgrade head`"
    )
