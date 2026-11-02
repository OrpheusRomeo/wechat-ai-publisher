#!/usr/bin/env bash
# 检查 DB schema 是否与 model 一致 (alembic check).
#
# 用法:
#   bash scripts/db_check.sh
#
# 返回码:
#   0 = clean, model 与 DB 一致
#   非 0 = 有漂移, 需要跑 `alembic revision --autogenerate` 生成迁移
#
# 适合 CI / pre-commit hook: 改了 model 后跑这个, 失败就提醒"还没生成迁移".
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T app alembic check
echo "✓ alembic check passed"