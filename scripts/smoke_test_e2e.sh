#!/bin/bash
# 端到端冒烟测试: mock 一次用户选择, 验证 Kafka 链路打通
set -e

PG="docker exec wechat-postgres psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher"

echo "===== 1. 取最新一个 PENDING topic ====="
TOPIC_ID=$($PG -t -A -c "SELECT id FROM hot_topics WHERE status='PENDING' ORDER BY id DESC LIMIT 1;")
if [ -z "$TOPIC_ID" ]; then
  echo "没有 PENDING 话题, 请先跑一次爬虫"; exit 1
fi
echo "    选中 topic_id = $TOPIC_ID"

echo
echo "===== 2. 写入 mock user_subscription ====="
$PG -c "
INSERT INTO user_subscriptions (user_id, openid, is_active, subscribe_time, last_interact_time)
VALUES (gen_random_uuid(), 'mock_openid_smoke', true, NOW(), NOW())
ON CONFLICT (openid) DO UPDATE SET is_active=true;
"

echo
echo "===== 3. 写入 mock user_choice (先清掉旧的, 再插新的) ====="
$PG -c "
DELETE FROM user_choices WHERE user_openid='mock_openid_smoke' AND topic_id=$TOPIC_ID;
INSERT INTO user_choices (choice_id, user_openid, topic_id, choice_index, is_processed, choose_time)
VALUES (gen_random_uuid(), 'mock_openid_smoke', $TOPIC_ID, NULL, false, NOW())
RETURNING id, choice_id;
"

echo
echo "===== 4. 直接调 process_user_choices (Celery 同步, 等它跑完) ====="
docker exec wechat-celery-worker \
  celery -A app.tasks.celery_app call app.tasks.article_tasks.process_user_choices 2>&1 | tail -20

echo
echo "===== 5. 看 Kafka generate-articles 是否有新消息 ====="
sleep 2
docker exec wechat-kafka /usr/bin/kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic generate-articles \
  --from-beginning --max-messages 1 \
  --timeout-ms 5000 2>&1 || echo "no message yet"

echo
echo "===== 6. 等 8 秒看 generate_consumer 是否消费成功 ====="
sleep 8
docker logs wechat-consumer-generate --tail 30

echo
echo "===== 7. 看 generated_articles 表 ====="
$PG -c "SELECT id, topic_id, status, title, wechat_media_id, publish_time FROM generated_articles ORDER BY id DESC LIMIT 3;"

echo
echo "===== 8. 看 Kafka publish-articles 是否有新消息 ====="
docker exec wechat-kafka /usr/bin/kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic publish-articles \
  --from-beginning --max-messages 1 \
  --timeout-ms 5000 2>&1 || echo "no message yet"

echo
echo "===== 9. 等 10 秒看 publish_consumer 是否消费成功 ====="
sleep 10
docker logs wechat-consumer-publish --tail 30

echo
echo "===== 10. 再看 generated_articles 是否变成 PUBLISHED ====="
$PG -c "SELECT id, topic_id, status, wechat_media_id, wechat_publish_id, publish_time FROM generated_articles ORDER BY id DESC LIMIT 3;"

echo
echo "===== 11. 看 push_consumer 是否推送模板消息 ====="
sleep 5
docker logs wechat-consumer-push --tail 20

echo
echo "===== 12. 最终 user_choices 状态 ====="
$PG -c "SELECT id, topic_id, user_openid, is_processed, process_time FROM user_choices ORDER BY id DESC LIMIT 3;"

echo
echo "===== 13. 最终 hot_topics 状态 ====="
$PG -c "SELECT id, title, status FROM hot_topics WHERE id=$TOPIC_ID;"