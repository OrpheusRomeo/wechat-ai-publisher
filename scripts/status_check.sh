#!/bin/bash
# 快速状态检查
echo "===== 容器状态 ====="
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>&1

echo
echo "===== ENABLE_AUTO_PUBLISH 在各容器里的值 ====="
for c in consumer-generate consumer-publish consumer-push celery-worker; do
  v=$(docker exec wechat-$c sh -c "env | grep ENABLE_AUTO_PUBLISH" 2>/dev/null)
  echo "  wechat-$c: $v"
done

echo
echo "===== consumer-generate 最近日志 ====="
docker logs wechat-consumer-generate --tail 5

echo
echo "===== consumer-publish 最近日志 ====="
docker logs wechat-consumer-publish --tail 5