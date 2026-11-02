"""merge heads: LLM providers + platform IP whitelist"""
from alembic import op
import sqlalchemy as sa

revision = "merge_platform_ip_whitelist"
down_revision = ("2026_08_14_0000", "create_platform_ip_whitelist")


def upgrade() -> None:
    pass  # merge migration: no-op, just marks convergence


def downgrade() -> None:
    pass  #撤销 merge
