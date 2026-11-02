# alembic/versions/2026_08_14_0000_create_llm_providers.py
"""Create llm_providers table"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '2026_08_14_0000'
down_revision = 'migrate_keyword_hash_sha256'  # 指向前一个迁移的 revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'llm_providers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(50), nullable=False),
        sa.Column('provider_type', sa.String(30), nullable=False),
        sa.Column('api_url', sa.String(255), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False, server_default=''),
        sa.Column('api_key_masked', sa.String(64), nullable=False, server_default=''),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('temperature', sa.Float(), server_default='0.7'),
        sa.Column('timeout', sa.Integer(), server_default='60'),
        sa.Column('extra_config', JSONB(), server_default='{}'),
        sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_providers_type_active', 'llm_providers', ['provider_type', 'is_active', 'is_deleted'])
    op.create_index('ix_llm_providers_default', 'llm_providers', ['is_default'],
                    postgresql_where=sa.text('is_deleted = false'))


def downgrade() -> None:
    op.drop_index('ix_llm_providers_default', table_name='llm_providers')
    op.drop_index('ix_llm_providers_type_active', table_name='llm_providers')
    op.drop_table('llm_providers')
