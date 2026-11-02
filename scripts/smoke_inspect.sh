#!/bin/bash
# 查 alembic 版本 + generated_articles choice_id 状态
set -e

PG="docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher"

echo "===== alembic 当前版本 ====="
$PG -c "SELECT * FROM alembic_version;"

echo
echo "===== 是否有 choice_id 列 ====="
$PG -c "\d generated_articles" | grep -E "choice_id|user_openid" || echo "!! choice_id / user_openid 列都不存在 !!"