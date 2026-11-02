"""fix baijiahao typo in platforms table

Revision ID: fix_baijiahao_typo
Revises: 0006
Create Date: 2026-08-13 00:00:00

纠正 platforms.platform_type 中的 baijiahhao -> baijiahao 拼写错误。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "fix_baijiahao_typo"
down_revision = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 修正 platform_type 列中的拼写错误
    op.execute("""
        UPDATE platforms
        SET platform_type = 'baijiahao'
        WHERE platform_type = 'baijiahhao'
    """)


def downgrade() -> None:
    # 回退到错误拼写（仅迁移层面，不影响业务数据）
    op.execute("""
        UPDATE platforms
        SET platform_type = 'baijiahhao'
        WHERE platform_type = 'baijiahao'
    """)
