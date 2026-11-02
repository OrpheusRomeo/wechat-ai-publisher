"""add FK to hot_topics, dedupe columns on generated_articles

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02 00:01:00

本次迁移做四件事，对应审核报告中的 #4 与 #15：

1. ``generated_articles`` 新增 ``choice_id`` 与 ``user_openid`` 两列：
   - ``choice_id`` 用于支撑 ``UniqueConstraint(topic_id, choice_id)``
     防止 Celery 与 Kafka 双路径对同一 (topic, choice) 重复生成文章
   - ``user_openid`` 用于发布成功后向 Kafka 发通知时定位推送目标
2. 清理孤儿行：删除 ``user_choices`` / ``generated_articles`` 中
   ``topic_id`` 已不在 ``hot_topics.id`` 中的行（FK 建立前必须先清理）
3. 清理重复行：保留每个 ``(topic_id, choice_id)`` 的最新一条，
   防止加唯一约束时失败
4. 加 FK 与唯一约束
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. 新增两列 ----
    op.add_column(
        "generated_articles",
        sa.Column("choice_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "generated_articles",
        sa.Column("user_openid", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_articles_choice_id",
        "generated_articles",
        ["choice_id"],
    )

    # ---- 2. 清理孤儿行 (FK 建立前必须先做) ----
    op.execute(
        """
        DELETE FROM user_choices
        WHERE topic_id NOT IN (SELECT id FROM hot_topics)
        """
    )
    op.execute(
        """
        DELETE FROM generated_articles
        WHERE topic_id NOT IN (SELECT id FROM hot_topics)
        """
    )

    # ---- 3. 清理 (topic_id, choice_id) 重复行 ----
    # 同一 (topic, choice) 保留 create_time 最新、id 最大的一条
    # choice_id IS NULL 不参与去重（NULL 不受唯一约束影响，可保留多行）
    op.execute(
        """
        DELETE FROM generated_articles
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY topic_id, choice_id
                           ORDER BY create_time DESC, id DESC
                       ) AS rn
                FROM generated_articles
                WHERE choice_id IS NOT NULL
            ) t
            WHERE t.rn > 1
        )
        """
    )

    # ---- 4. 加 FK 与唯一约束 ----
    op.create_foreign_key(
        "fk_user_choices_topic_id",
        "user_choices",
        "hot_topics",
        ["topic_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_generated_articles_topic_id",
        "generated_articles",
        "hot_topics",
        ["topic_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_articles_topic_choice",
        "generated_articles",
        ["topic_id", "choice_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_articles_topic_choice", "generated_articles", type_="unique")
    op.drop_constraint(
        "fk_generated_articles_topic_id", "generated_articles", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_user_choices_topic_id", "user_choices", type_="foreignkey"
    )

    op.drop_index("ix_articles_choice_id", table_name="generated_articles")
    op.drop_column("generated_articles", "user_openid")
    op.drop_column("generated_articles", "choice_id")