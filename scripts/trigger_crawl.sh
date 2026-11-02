#!/bin/bash
# 触发爬虫任务
echo "===== 调用 crawl_hot_topics (Celery) ====="
docker exec wechat-celery-worker \
  celery -A app.tasks.celery_app call app.tasks.crawl_tasks.crawl_hot_topics 2>&1 | tail -5

echo
echo "===== 等 5 秒看 hot_topics ====="
sleep 5
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT COUNT(*) AS total, COUNT(CASE WHEN status='PENDING' THEN 1 END) AS pending FROM hot_topics;"