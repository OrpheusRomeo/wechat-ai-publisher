#!/bin/bash
# 重建后状态检查
echo "===== 容器状态 ====="
docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>&1

echo
echo "===== migrate 退出码 ====="
docker inspect wechat-migrate --format='{{.State.ExitCode}}' 2>&1

echo
echo "===== migrate 日志 ====="
docker logs wechat-migrate --tail 10

echo
echo "===== ENABLE_AUTO_PUBLISH 在容器里的值 ====="
for c in consumer-generate consumer-publish consumer-push celery-worker; do
  v=$(docker exec wechat-$c sh -c "env | grep ENABLE_AUTO_PUBLISH" 2>/dev/null)
  echo "  wechat-$c: $v"
done

echo
echo "===== alembic version ====="
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT * FROM alembic_version;"