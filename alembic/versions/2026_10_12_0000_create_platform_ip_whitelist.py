"""Create platform_ip_whitelist table

平台 IP 白名单：各平台（微信/抖音/头条/小红书/油管）对外调用时的
出站 IP 白名单，与管理后台访问控制的 ip_whitelist 表完全独立。
"""
from alembic import op
import sqlalchemy as sa

revision = "create_platform_ip_whitelist"
down_revision = "migrate_keyword_hash_sha256"  # 沿用 keyword hash 迁移的 revision
# down_revision = None  # 如果没有待运行的迁移则设 None

def upgrade() -> None:
    op.create_table(
        "platform_ip_whitelist",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("platform_type", sa.String(32), nullable=False, index=True),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False, server_default="''"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_auto_synced", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("platform_type", "ip_address", name="uq_platform_ip"),
        sa.Index("ix_piw_platform_active", "platform_type", "is_active"),
    )


def downgrade() -> None:
    op.drop_table("platform_ip_whitelist")
