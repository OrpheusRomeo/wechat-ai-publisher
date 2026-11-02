"""add platforms and platform_accounts tables

Revision ID: 9916a663457e
Revises: 5861f005e42d
Create Date: 2026-08-10 00:00:00

新增:
- platforms: 平台定义表（微信公众号、抖音、小红书等）
- platform_accounts: 平台账号表（支持多账号多平台）

同时:
- hot_topics 增加 platform_account_id FK（可空，NULL 表示全局/legacy）
- generated_articles 增加 platform_account_id FK 和 platform_media_id（可空）
- ip_whitelist 增加 account_id FK（可空，NULL 表示全局白名单）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) platforms 表
    op.create_table(
        "platforms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("icon", sa.String(128), server_default="", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platforms")),
    )
    op.create_index("ix_platforms_type", "platforms", ["platform_type"], unique=True)
    op.create_index("ix_platforms_type_active", "platforms", ["platform_type", "is_active"])

    # 2) platform_accounts 表
    op.create_table(
        "platform_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("account_name", sa.String(128), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), server_default="", nullable=False),
        sa.Column("credentials_masked", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("crawl_cron", sa.String(32), server_default="08:00", nullable=False),
        sa.Column("push_cron", sa.String(32), server_default="09:00", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], name=op.f("fk_platform_accounts_platform_id"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_accounts")),
    )
    op.create_index("ix_platform_accounts_account_id", "platform_accounts", ["account_id"], unique=True)
    op.create_index("ix_platform_accounts_platform_deleted", "platform_accounts", ["platform_id", "is_deleted"])
    op.create_index("ix_platform_accounts_active", "platform_accounts", ["is_active", "is_deleted"])

    # 3) hot_topics 增加 platform_account_id
    op.add_column("hot_topics", sa.Column("platform_account_id", sa.Integer(), nullable=True))
    op.create_index("ix_hot_topics_account", "hot_topics", ["platform_account_id"])
    op.create_index("ix_hot_topics_account_status", "hot_topics", ["platform_account_id", "status"])
    op.create_foreign_key(
        "fk_hot_topics_platform_account_id",
        "hot_topics", "platform_accounts",
        ["platform_account_id"], ["id"],
        ondelete="SET NULL"
    )

    # 4) generated_articles 增加 platform_account_id 和 platform_media_id
    op.add_column("generated_articles", sa.Column("platform_account_id", sa.Integer(), nullable=True))
    op.add_column("generated_articles", sa.Column("platform_media_id", sa.String(256), nullable=True))
    op.create_index("ix_articles_platform_account", "generated_articles", ["platform_account_id", "status"])
    op.create_foreign_key(
        "fk_articles_platform_account_id",
        "generated_articles", "platform_accounts",
        ["platform_account_id"], ["id"],
        ondelete="SET NULL"
    )

    # 5) ip_whitelist 增加 account_id
    op.add_column("ip_whitelist", sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_ip_whitelist_account", "ip_whitelist", ["account_id"])
    op.create_index("ix_ip_whitelist_account_enabled", "ip_whitelist", ["account_id", "enabled"])
    op.create_foreign_key(
        "fk_ip_whitelist_account_id",
        "ip_whitelist", "platform_accounts",
        ["account_id"], ["account_id"],
        ondelete="CASCADE"
    )

    # 6) 初始化默认平台和账号（从 system_settings 迁移微信凭证）
    _seed_default_platform_and_account(op)


def downgrade() -> None:
    # 移除 FK 和索引
    op.drop_constraint("fk_ip_whitelist_account_id", "ip_whitelist", type_="foreignkey")
    op.drop_index("ix_ip_whitelist_account_enabled", "ip_whitelist")
    op.drop_index("ix_ip_whitelist_account", "ip_whitelist")
    op.drop_column("ip_whitelist", "account_id")

    op.drop_constraint("fk_articles_platform_account_id", "generated_articles", type_="foreignkey")
    op.drop_index("ix_articles_platform_account", "generated_articles")
    op.drop_column("generated_articles", "platform_media_id")
    op.drop_column("generated_articles", "platform_account_id")

    op.drop_constraint("fk_hot_topics_platform_account_id", "hot_topics", type_="foreignkey")
    op.drop_index("ix_hot_topics_account_status", "hot_topics")
    op.drop_index("ix_hot_topics_account", "hot_topics")
    op.drop_column("hot_topics", "platform_account_id")

    op.drop_table("platform_accounts")
    op.drop_index("ix_platforms_type_active", "platforms")
    op.drop_index("ix_platforms_type", "platforms")
    op.drop_table("platforms")


def _seed_default_platform_and_account(op):
    """初始化默认的微信公众号平台和账号。

    凭证从 system_settings 读取，迁移到 platform_accounts 表。
    如果 system_settings 中没有微信凭证，则只创建平台记录。
    """
    from cryptography.fernet import Fernet

    now_sql = "NOW()"

    # 插入微信平台
    op.execute("""
        INSERT INTO platforms (platform_type, display_name, icon, is_active, created_at)
        VALUES ('wechat', '微信公众号', 'wechat', true, NOW())
        ON CONFLICT (platform_type) DO NOTHING
    """)

    # 尝试从 system_settings 获取微信凭证
    # 注意：这里只是占位，实际凭证需要在部署时或通过 API 设置
    # 因为 system_settings 中的值可能是加密的，需要业务逻辑来处理
    op.execute("""
        INSERT INTO platform_accounts
            (account_id, platform_id, account_name, credentials_encrypted, credentials_masked,
             crawl_cron, push_cron, config, is_active, is_deleted, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            p.id,
            '默认账号',
            '',
            '{"app_id": "to_be_configured"}',
            '08:00',
            '09:00',
            '{"daily_publish_quota": 1}',
            true,
            false,
            NOW(),
            NOW()
        FROM platforms p WHERE p.platform_type = 'wechat'
        ON CONFLICT DO NOTHING
    """)
