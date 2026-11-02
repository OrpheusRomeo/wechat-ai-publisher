#!/bin/bash
# 端到端冒烟测试: 看现状
set -e

echo "===== 1. Kafka 消费者组 ====="
docker exec wechat-kafka /usr/bin/kafka-consumer-groups \
  --bootstrap-server localhost:9092 --list

echo
echo "===== 2. Kafka topic 列表 ====="
docker exec wechat-kafka /usr/bin/kafka-topics \
  --bootstrap-server localhost:9092 --list

echo
echo "===== 3. 各 consumer 当前的 lag ====="
for GROUP in wechat-consumer-group-push wechat-consumer-group-generate wechat-consumer-group-publish; do
  echo "--- $GROUP ---"
  docker exec wechat-kafka /usr/bin/kafka-consumer-groups \
    --bootstrap-server localhost:9092 \
    --describe --group "$GROUP" 2>&1 | head -20
done

echo
echo "===== 4. 现有 hot_topics ====="
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT id, title, status FROM hot_topics ORDER BY id DESC LIMIT 5;"

echo
echo "===== 5. 现有 user_choices ====="
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT id, topic_id, user_openid, is_processed FROM user_choices ORDER BY id DESC LIMIT 5;"

echo
echo "===== 6. 现有 generated_articles ====="
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT id, topic_id, status, wechat_media_id, publish_time FROM generated_articles ORDER BY id DESC LIMIT 5;"

echo
echo "===== 7. user_subscriptions ====="
docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
  -c "SELECT id, openid, is_active, subscribe_time FROM user_subscriptions ORDER BY id DESC LIMIT 5;"

echo
echo "===== 8. consumer-publish 启动日志 ====="
docker logs wechat-consumer-publish --tail 5
echo
echo "===== 9. consumer-generate 最近日志 ====="
docker logs wechat-consumer-generate --tail 5