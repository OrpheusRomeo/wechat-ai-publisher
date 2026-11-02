"""add admin_users, system_settings, ip_whitelist tables + make article.topic_id nullable

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-24 00:00:00

新增:
- admin_users: 管理后台多用户认证 (JWT)
- system_settings: 在线修改的系统配置 (敏感值 AES 加密)
- ip_whitelist: API IP 白名单

同时:
- generated_articles.topic_id 改为可空（关键词深度文章不关联热搜话题）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) generated_articles.topic_id → nullable (关键词文章无 topic)
    op.alter_column(
        "generated_articles",
        "topic_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # 2) admin_users
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="admin"),
        sa.Column("nickname", sa.String(64), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)

    # 3) system_settings
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("value_masked", sa.String(128), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="system"),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("placeholder", sa.String(256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )
    op.create_index("ix_system_settings_category", "system_settings", ["category"])

    # 4) ip_whitelist
    op.create_table(
        "ip_whitelist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ip_or_cidr", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ip_whitelist")),
    )
    op.create_index("ix_ip_whitelist_ip", "ip_whitelist", ["ip_or_cidr"], unique=True)

    # 5) 初始化默认管理员 (admin / admin123)
    from app.core.security import hash_password
    admin_hash = hash_password("admin123")
    op.execute(
        f"""
        INSERT INTO admin_users (username, password_hash, role, nickname)
        VALUES ('admin', '{admin_hash}', 'superadmin', '超级管理员')
        ON CONFLICT (username) DO NOTHING
        """
    )

    # 6) 初始化 system_settings 默认行
    _seed_settings(op)


def downgrade() -> None:
    op.drop_table("ip_whitelist")
    op.drop_table("system_settings")
    op.drop_index("ix_admin_users_username", table_name="admin_users")
    op.drop_table("admin_users")
    op.alter_column(
        "generated_articles",
        "topic_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def _seed_settings(op):
    """写入 system_settings 默认行（把 .env 中可在线修改的项同步一份进去）。"""
    now_sql = "NOW()"
    rows = [
        # === wechat ===
        ("wechat_app_id", "", "", "wechat", "微信公众平台 AppID", True, "如 wx1234567890abcdef"),
        ("wechat_app_secret", "", "", "wechat", "微信公众平台 AppSecret", True, "从公众号后台获取"),
        ("wechat_default_cover_url", "https://picsum.photos/seed/wechat-digest/900/383", "", "wechat", "默认封面图 URL", False, "建议 2.35:1 比例"),
        ("wechat_daily_publish_quota", "1", "", "wechat", "每日发布配额", False, "个人订阅号 1，认证 4"),
        ("wechat_enable_custom_message", "true", "", "wechat", "启用客服消息", False, "true/false"),
        ("wechat_digest_color_title", "#2C5F8D", "", "wechat", "日报标题色", False, "#RRGGBB"),
        ("wechat_digest_color_brief", "#5F8D4E", "", "wechat", "日报简讯色", False, "#RRGGBB"),
        ("wechat_digest_color_link", "#7D2E8E", "", "wechat", "日报链接色", False, "#RRGGBB"),
        # === deepseek ===
        ("deepseek_api_key", "", "sk-0****abcd", "deepseek", "DeepSeek API Key", True, "sk-..."),
        ("deepseek_model", "deepseek-chat", "", "deepseek", "DeepSeek 模型", False, "deepseek-chat"),
        ("deepseek_temperature", "0.7", "", "deepseek", "生成温度", False, "0.0-1.0"),
        ("deepseek_max_tokens", "3000", "", "deepseek", "最大 Token 数", False, "整数"),
        # === scheduler ===
        ("scheduler_crawl_time", "08:00", "", "scheduler", "爬取定时 (HH:MM)", False, "如 08:00"),
        ("scheduler_push_time", "09:00", "", "scheduler", "推送定时 (HH:MM)", False, "如 09:00"),
        ("scheduler_topic_limit", "10", "", "scheduler", "话题数量上限", False, "整数"),
        # === system ===
        ("enable_crawler", "true", "", "system", "启用爬虫", False, "true/false"),
        ("enable_auto_publish", "false", "", "system", "生成后自动发布", False, "true/false"),
        ("enable_deepseek", "true", "", "system", "启用 AI 生成", False, "true/false"),
        ("baidu_hot_use_mock", "false", "", "system", "爬虫使用 Mock", False, "true/false"),
    ]
    for key, enc, masked, cat, desc, is_sec, placeholder in rows:
        op.execute(
            f"""
            INSERT INTO system_settings
                (key, value_encrypted, value_masked, category, description, is_secret, placeholder, updated_at)
            VALUES ('{key}', '{enc}', '{masked}', '{cat}', '{desc}', {'true' if is_sec else 'false'}, '{placeholder}', {now_sql})
            ON CONFLICT (key) DO NOTHING
            """
        )
