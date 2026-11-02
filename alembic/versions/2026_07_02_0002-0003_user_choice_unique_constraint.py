"""add user_choice unique constraint on (user_openid, topic_id)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-02 00:02:00

修复 M1: 修复 POST /api/v1/topics/choice 的 check-then-insert TOCTOU 竞态,
DB 层强制 ``(user_openid, topic_id)`` 唯一. 部署此迁移前必须先清理重复行.

清理策略:
- 同一 ``(user_openid, topic_id)`` 保留 ``id`` 最大的一条 (即最近插入),
  其余 ``DELETE FROM user_choices``
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 清理 (user_openid, topic_id) 重复行, 仅保留 id 最大
    op.execute(
        """
        DELETE FROM user_choices
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_openid, topic_id
                           ORDER BY id DESC
                       ) AS rn
                FROM user_choices
            ) t
            WHERE t.rn > 1
        )
        """
    )
    # 2) 加唯一约束
    op.create_unique_constraint(
        "uq_user_choice_openid_topic",
        "user_choices",
        ["user_openid", "topic_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_choice_openid_topic",
        "user_choices",
        type_="unique",
    )
