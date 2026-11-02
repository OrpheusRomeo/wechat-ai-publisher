"""migrate keyword_hash from SHA1 to SHA256

Revision ID: migrate_keyword_hash_sha256
Revises: fix_baijiahao_typo
Create Date: 2026-08-13 00:01:00

将所有已存在的关键词文章 metadata["keyword_hash"] 从 SHA1 格式
升级为 SHA256 格式。

旧: SHA1(keyword + article_create_date)[:12]
新: SHA256(keyword + today_str)[:12]   (today_str = 今天日期 "YYYY-MM-DD")

今日首次运行新代码时会为每篇关键词文章重新计算哈希，如果不迁移，
旧记录会因为哈希不匹配而被视为"不存在"，导致重复生成。
迁移后新代码能找到这些文章，幂等检查通过，不会重复生成。

此迁移只执行一次，之后新创建的文章直接用 SHA256。
"""
import hashlib
import json
from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "migrate_keyword_hash_sha256"
down_revision = "fix_baijiahao_typo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sha256_keyword_hash(keyword: str, day: date) -> str:
    """计算 SHA256 关键词哈希，与 article_tasks.generate_deep_article 保持一致."""
    return hashlib.sha256(f"{keyword}:{day.isoformat()}".encode()).hexdigest()[:12]


def upgrade() -> None:
    today = date.today()

    # 取出所有含 keyword_hash 的文章记录
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("""
            SELECT id, metadata
            FROM generated_articles
            WHERE metadata IS NOT NULL
              AND metadata->>'keyword_hash' IS NOT NULL
        """)
    ).fetchall()

    updated = 0
    for row in rows:
        article_id = row[0]
        meta = dict(row[1]) if not isinstance(row[1], dict) else row[1]
        keyword = meta.get("keyword", "")

        if not keyword:
            continue

        # 重新计算 SHA256 哈希（日期取今天，与新代码逻辑一致）
        new_hash = _sha256_keyword_hash(keyword, today)
        meta["keyword_hash"] = new_hash

        conn.execute(
            sa.text("""
                UPDATE generated_articles
                SET metadata = :meta::jsonb
                WHERE id = :id
            """),
            {"meta": json.dumps(meta), "id": article_id},
        )
        updated += 1

    print(f"[迁移] keyword_hash SHA1->SHA256 完成, 更新 {updated} 行")


def downgrade() -> None:
    # 不需要回退：旧 SHA1 值已丢失，无法还原
    pass
