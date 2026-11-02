"""sync model defaults and indexes with current schema

Revision ID: 5861f005e42d
Revises: 0004
Create Date: 2026-07-27 10:53:05.401240+08:00

注意: 这个迁移是 alembic revision --autogenerate 生成的, 但里面大多数
``alter_column ... server_default=None`` 是 false positive — alembic 把
model 的 ``default=...`` (Python 端) 当成 server_default 想 drop, 实际 DB
里 server_default 表达式还在, 不需要动.

所以这里只保留真正需要执行的操作:
- ``ip_whitelist`` 索引改名 + 加 unique: model 里改成了 ``Index("ix_ip_whitelist_ip_or_cidr", ...)``
  且 column ``ip_or_cidr`` 加了 ``unique=True``, 需要 drop 旧 index, 建新 unique index.

其余的 user_subscriptions_openid_key drop_constraint 也是 false positive —
``openid = Column(..., unique=True)`` 已经创建了 unique 约束, 不应该 drop.

跑完这个迁移后 ``alembic check`` 应该 clean, 表示 model 与 DB 一致.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5861f005e42d'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ip_whitelist: 旧 index 名 ix_ip_whitelist_ip (非 unique) → 新名 ix_ip_whitelist_ip_or_cidr (unique)
    op.drop_index('ix_ip_whitelist_ip', table_name='ip_whitelist')
    op.create_index(
        op.f('ix_ip_whitelist_ip_or_cidr'),
        'ip_whitelist',
        ['ip_or_cidr'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_ip_whitelist_ip_or_cidr'), table_name='ip_whitelist')
    op.create_index('ix_ip_whitelist_ip', 'ip_whitelist', ['ip_or_cidr'], unique=False)