"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-02 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- hot_topics ----
    op.create_table(
        "hot_topics",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("heat_score", sa.Float, nullable=False),
        sa.Column("source_url", sa.String(length=500)),
        sa.Column("summary", sa.Text),
        sa.Column("category", sa.String(length=50)),
        sa.Column("keywords", postgresql.JSONB, default=list),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "PUSHED", "SELECTED", "PROCESSED", "FAILED",
                name="topicstatus",
            ),
            default="PENDING",
        ),
        sa.Column("metadata", postgresql.JSONB, default=dict),
        sa.Column("create_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("push_time", sa.DateTime),
    )
    op.create_index("ix_hot_topics_status_create", "hot_topics", ["status", "create_time"])
    op.create_index("ix_hot_topics_heat", "hot_topics", ["heat_score"])

    # ---- user_choices ----
    op.create_table(
        "user_choices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("choice_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_openid", sa.String(length=100), nullable=False),
        sa.Column("topic_id", sa.Integer, nullable=False),
        sa.Column("choice_index", sa.Integer),
        sa.Column("choose_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("is_processed", sa.Boolean, default=False),
        sa.Column("process_time", sa.DateTime),
    )
    op.create_index(
        "ix_user_choices_user_processed",
        "user_choices",
        ["user_openid", "is_processed"],
    )
    op.create_index("ix_user_choices_topic", "user_choices", ["topic_id"])

    # ---- generated_articles ----
    op.create_table(
        "generated_articles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("topic_id", sa.Integer, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("cover_url", sa.String(length=500)),
        sa.Column("tags", postgresql.JSONB, default=list),
        sa.Column(
            "status",
            sa.Enum(
                "GENERATING", "GENERATED", "PUBLISHING", "PUBLISHED", "DRAFT", "FAILED",
                name="articlestatus",
            ),
            default="GENERATING",
        ),
        sa.Column("wechat_media_id", sa.String(length=100)),
        sa.Column("wechat_publish_id", sa.String(length=100)),
        sa.Column("error_msg", sa.Text),
        sa.Column("metadata", postgresql.JSONB, default=dict),
        sa.Column("create_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("publish_time", sa.DateTime),
    )
    op.create_index(
        "ix_articles_status_create",
        "generated_articles",
        ["status", "create_time"],
    )
    op.create_index(
        "ix_articles_wechat_media",
        "generated_articles",
        ["wechat_media_id"],
    )

    # ---- user_subscriptions ----
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("openid", sa.String(length=100), nullable=False, unique=True),
        sa.Column("unionid", sa.String(length=100)),
        sa.Column("nickname", sa.String(length=100)),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("subscribe_time", sa.DateTime, server_default=sa.func.now()),
        sa.Column("last_interact_time", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_user_subscriptions_openid", "user_subscriptions", ["openid"], unique=True
    )
    op.create_index(
        "ix_user_subscriptions_unionid", "user_subscriptions", ["unionid"]
    )
    op.create_index(
        "ix_user_subs_active_last",
        "user_subscriptions",
        ["is_active", "last_interact_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_subs_active_last", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_unionid", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_openid", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")

    op.drop_index("ix_articles_wechat_media", table_name="generated_articles")
    op.drop_index("ix_articles_status_create", table_name="generated_articles")
    op.drop_table("generated_articles")
    op.execute("DROP TYPE IF EXISTS articlestatus")

    op.drop_index("ix_user_choices_topic", table_name="user_choices")
    op.drop_index("ix_user_choices_user_processed", table_name="user_choices")
    op.drop_table("user_choices")

    op.drop_index("ix_hot_topics_heat", table_name="hot_topics")
    op.drop_index("ix_hot_topics_status_create", table_name="hot_topics")
    op.drop_table("hot_topics")
    op.execute("DROP TYPE IF EXISTS topicstatus")
