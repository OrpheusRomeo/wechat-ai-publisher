#!/bin/bash
# scripts/backup.sh - 备份 PostgreSQL 到 ./backups/<时间戳>.sql.gz
set -e

mkdir -p backups
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT="backups/postgres_${TIMESTAMP}.sql.gz"

# 仅在容器启动时执行
if docker compose ps postgres >/dev/null 2>&1; then
  docker compose exec -T postgres \
    pg_dump -U "${PG_USER:-wechat_user}" "${PG_DB:-wechat_ai_publisher}" \
    | gzip > "$OUT"
  echo "已备份: $OUT"
else
  # 直接在主机端
  PGPASSWORD="${PG_PASSWORD:-wechat_password}" pg_dump \
    -h "${PG_HOST:-localhost}" -p "${PG_PORT:-5555}" \
    -U "${PG_USER:-wechat_user}" "${PG_DB:-wechat_ai_publisher}" \
    | gzip > "$OUT"
  echo "已备份: $OUT"
fi
