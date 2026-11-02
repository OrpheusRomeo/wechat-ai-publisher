"""Alembic 环境配置：从 app.core.config 读 DB URL，注册所有 model 的 metadata。"""
import os
import sys
from logging.config import fileConfig

from alembic import context

# 让 alembic 命令能找到 app 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import engine_from_config, pool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402

# 触发所有 model 的 import，确保 Base.metadata 收集到全部表
import app.models  # noqa: F401,E402  (import 所有 models)
from app.models.topic import HotTopic, UserChoice, TopicStatus  # noqa: F401
from app.models.article import GeneratedArticle, ArticleStatus  # noqa: F401
from app.models.user import UserSubscription  # noqa: F401
from app.models.admin_user import AdminUser  # noqa: F401
from app.models.system_setting import SystemSetting  # noqa: F401
from app.models.ip_whitelist import IPWhitelist  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用项目里的 ``settings.pg_*`` 拼出 DB URL，注入 alembic
config.set_main_option(
    "sqlalchemy.url",
    f"postgresql://{settings.pg_user}:{settings.pg_password}"
    f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}",
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连 DB 执行。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
