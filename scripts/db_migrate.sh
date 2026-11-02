#!/usr/bin/env bash
# 应用所有未执行的 Alembic 迁移 (alembic upgrade head).
#
# 用法:
#   bash scripts/db_migrate.sh
#
# 部署场景:
#   - 手动部署: 跑这个就行, 比 `docker compose up -d migrate` 简单 (migrate 是 docker compose 的一次性服务)
#   - 自动部署: deploy.sh 已经调用 docker compose, 会自动跑 migrate service
#
# 跑完后建议再跑 `db_check.sh` 确认 clean.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T app alembic upgrade head
echo "✓ alembic upgrade head completed"
echo "→ 建议跑 bash scripts/db_check.sh 确认 model 与 DB 一致"