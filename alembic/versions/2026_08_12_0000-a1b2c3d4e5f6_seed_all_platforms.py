"""seed all supported platforms into the platforms table + merge migration heads

Revision ID: a1b2c3d4e5f6
Revises: 0005, 5861f005e42d
Create Date: 2026-08-12 00:00:00

目前 migration 0005 只 seed 了 wechat 平台。本 migration 用 idempotent
INSERT ... ON CONFLICT DO NOTHING 把全部 6 个平台补齐，包括 wechat 本身
（已存在的行不会受影响）。

同时作为 merge migration 合并两条分支:
- 0005 (platforms + platform_accounts)
- 5861f005e42d (sync model defaults and indexes)

平台列表（与 app/models/platform.py:PlatformType 对齐）:

| platform_type | display_name | icon      |
| wechat        | 微信公众号    | wechat    |
| toutiao       | 今日头条      | reading   |
| douyin        | 抖音         | video     |
| xiaohongshu   | 小红书       | star      |
| baijiahhao    | 百度百家号    | document  |
| youtube       | YouTube     | video-play|
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision = ("0005", "5861f005e42d")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO platforms (platform_type, display_name, icon, is_active, created_at)
        VALUES
            ('wechat',      '微信公众号',  'wechat',     true, NOW()),
            ('toutiao',     '今日头条',    'reading',    true, NOW()),
            ('douyin',      '抖音',        'video',      true, NOW()),
            ('xiaohongshu', '小红书',      'star',       true, NOW()),
            ('baijiahhao',  '百度百家号',  'document',   true, NOW()),
            ('youtube',     'YouTube',     'video-play', true, NOW())
        ON CONFLICT (platform_type) DO NOTHING
    """)


def downgrade() -> None:
    # 不回退 platform 种子数据 — 依赖这些行的 FK 可能已经存在,
    # 回退会导致级联问题。
    # 如果确实要清理, 请在 SQL console 中手动操作。
    pass
