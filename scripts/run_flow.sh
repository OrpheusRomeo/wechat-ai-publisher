#!/bin/bash
# =====================================================================
# scripts/run_flow.sh
# 一气呵成走一遍完整流程, 不写日志不打分, 触发即结束.
# =====================================================================
set -u
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
set -a; . ./.env; set +a

: "${ADMIN_API_KEY:?ADMIN_API_KEY 未在 .env 中}"

API="http://localhost:8888/api/v1"
PG_PORT="${PG_PORT:-5555}"
PG_USER="${PG_USER:-wechat_user}"
PG_DB="${PG_DB:-wechat_ai_publisher}"

# 修复: PG 容器 listen 在 PG_PORT (5555), psql 默认找 .s.PGSQL.5432 失败.
# 用 -h + -p 走 TCP, 不要靠 unix socket.
PSQL=(docker compose exec -T -e PGPASSWORD="${PG_PASSWORD:-}" postgres psql -h localhost -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB")

run_sql() {
    "${PSQL[@]}" "$@"
}

run_sql_tA() {
    # -tA: tuples-only, unaligned. 末尾自动去空白
    "${PSQL[@]}" -tA "$@"
}

echo "==[ Step 1: 触发爬取 ]================================"
curl -sS -X POST "$API/topics/crawl" -H "X-Admin-Token: $ADMIN_API_KEY"
echo
echo "    等 5s 让 Celery 跑完..."
sleep 5

echo "==[ Step 2: 触发推送 ]================================"
# user_subscriptions.user_id 是 NOT NULL UUID, model 的 default=uuid.uuid4 是
# Python 端 default, 绕过 ORM 直接 INSERT 时不会自动生成, 必须显式给
# (这里 last_interact_time 也设 NOW(), 让 48h 推送窗口预检命中)
run_sql -c "
INSERT INTO user_subscriptions (user_id, openid, is_active, last_interact_time)
VALUES (gen_random_uuid(), 'flow_demo_openid', true, NOW())
ON CONFLICT (openid) DO UPDATE
  SET is_active=true, last_interact_time=NOW();
" >/dev/null

curl -sS -X POST "$API/topics/push" -H "X-Admin-Token: $ADMIN_API_KEY"
echo
echo "    等 5s 让 push_consumer 处理..."
sleep 5

echo "==[ Step 3: 用户选择第一条话题 ]======================="
TOPIC_ID=$(run_sql_tA -c "SELECT id FROM hot_topics WHERE create_time >= CURRENT_DATE ORDER BY heat_score DESC LIMIT 1;" 2>/dev/null | tr -d '[:space:]')
echo "    TOPIC_ID=${TOPIC_ID:-<空, 看 celery-worker --tail>}"

if [ -n "$TOPIC_ID" ]; then
  curl -sS -X POST "$API/topics/choice" \
    -H "X-Admin-Token: $ADMIN_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"topic_id\": $TOPIC_ID, \"choice_index\": 1, \"user_openid\": \"flow_demo_openid\"}"
  echo
fi

echo "==[ Step 4: 触发 process_user_choices 立即跑 ]======="
docker compose exec -T celery-worker \
  celery -A app.tasks.celery_app call app.tasks.article_tasks.process_user_choices 2>&1 | tail -3
echo "    等 30s 让 DeepSeek 生成 + 落库..."
sleep 30

echo "==[ Step 4b: 看生成了什么 ]============================="
run_sql -c "SELECT id, title, status, length(content) AS bytes, wechat_media_id FROM generated_articles ORDER BY id DESC LIMIT 3;"

echo "==[ Step 5: 发布最新一篇 status=generated 的文章 ]===="
ARTICLE_ID=$(run_sql_tA -c "SELECT id FROM generated_articles WHERE status='generated' ORDER BY id DESC LIMIT 1;" 2>/dev/null | tr -d '[:space:]')

if [ -n "$ARTICLE_ID" ]; then
  curl -sS -X POST "$API/articles/$ARTICLE_ID/publish" \
    -H "X-Admin-Token: $ADMIN_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"article_id\": $ARTICLE_ID, \"publish_type\": \"manual\"}"
  echo
  echo "    等 20s 让 publish_article 跑完..."
  sleep 20

  echo "==[ Step 5b: 看发布结果 ]============================="
  run_sql -c "SELECT id, status, wechat_media_id, wechat_publish_id, publish_time, error_msg FROM generated_articles WHERE id=$ARTICLE_ID;"
else
  echo "    没有 status=generated 的文章; 看 celery-worker --tail"
fi

echo "==[ Step 6: 看推送 publish-success 是否到了 push_consumer ]"
echo "    docker compose logs consumer-push --tail 20 看一下:"
echo "      订阅号没有模板消息, 走 send_custom_message 客服消息"
echo "      成功: '推送成功: user=...'"
echo "      失败 (用户不在 48h 窗口): '用户不在 48h 互动窗口, 跳过推送'"

echo
echo "===== 流程结束, 全链日志汇总 ====="
echo "  docker compose logs celery-worker   --tail 100"
echo "  docker compose logs consumer-push   --tail 50"
echo "  docker compose logs consumer-generate --tail 50"
