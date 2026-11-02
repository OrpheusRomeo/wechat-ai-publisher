#!/bin/bash
# =====================================================================
# scripts/e2e_verify.sh
# 端到端冒烟测试 — 验证 1) 爬取 2) 推送 3) 选话题 4) DeepSeek 生成
# 5) 公众号草稿 6) 发布 这一条完整链路
#
# 用法:
#   bash scripts/e2e_verify.sh
#
# 期望: 大多数步骤应该成功; 只有 4 / 5 / 6 依赖外部 API (DeepSeek + 微信)
#       在 mock 环境可能返回失败 — 这是预期, 不是脚本问题.
# =====================================================================
set -u  # 不要 set -e — 我们要让脚本继续跑完, 看到所有步骤的状态

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------
GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
BLUE="\033[34m"
RESET="\033[0m"

ok()    { echo -e "${GREEN}[PASS]${RESET} $*"; }
bad()   { echo -e "${RED}[FAIL]${RESET} $*"; }
note()  { echo -e "${BLUE}[INFO]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
hr()    { echo -e "\n${YELLOW}----- $* -----${RESET}"; }

# ---------------------------------------------------------------------
# 0. 准备: 拿 ADMIN_API_KEY, 检查 8888 是否通
# ---------------------------------------------------------------------
hr "0. 准备"

if [ ! -f .env ]; then
  bad ".env 不存在; cp .env.example .env 然后改密钥"
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a
note "ADMIN_API_KEY=${ADMIN_API_KEY:0:8}... (前 8 位)"
note "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:0:8}..."

if ! curl -fsS -m 3 http://localhost:8888/health >/dev/null 2>&1; then
  bad "API 不可达: http://localhost:8888/health"
  note "若服务没起: docker compose up -d"
  exit 1
fi
ok "API 健康: $(curl -sS http://localhost:8888/health | head -c 80)"

# 数据库 reachable?
if ! docker compose exec -T postgres pg_isready -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" >/dev/null 2>&1; then
  bad "Postgres 不可达"
  exit 1
fi
ok "Postgres 就绪"

# ---------------------------------------------------------------------
# 1. 触发爬取
# ---------------------------------------------------------------------
hr "1. 触发百度热搜爬取"

resp=$(curl -sS -X POST http://localhost:8888/api/v1/topics/crawl \
  -H "X-Admin-Token: $ADMIN_API_KEY")
note "POST /crawl 响应: $resp"

sleep 5  # 让 Celery 跑完

TOPIC_COUNT=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
  -c "SELECT COUNT(*) FROM hot_topics WHERE create_time >= CURRENT_DATE;" 2>/dev/null | tr -d '[:space:]')

if [ "${TOPIC_COUNT:-0}" -ge 1 ]; then
  ok "今日抓到 $TOPIC_COUNT 条话题"
else
  bad "今日没话题入库; 看 crawler 日志"
  warn "查看: docker compose logs celery-worker --tail 30"
  warn "网络问题可设 .env 的 BAIDU_HOT_USE_MOCK=true 重试"
fi

# 选一条 PENDING 的热 topic
TOPIC_ID=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
  -c "SELECT id FROM hot_topics WHERE create_time >= CURRENT_DATE ORDER BY heat_score DESC LIMIT 1;" 2>/dev/null | tr -d '[:space:]')
note "选定的 TOPIC_ID=$TOPIC_ID"

# ---------------------------------------------------------------------
# 2. 触发推送
# ---------------------------------------------------------------------
hr "2. 触发推送任务"

# 先确保 user_subscriptions 有活跃用户 (否则 push 任务不会发任何东西)
HAS_USER=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
  -c "SELECT COUNT(*) FROM user_subscriptions WHERE is_active=true;" 2>/dev/null | tr -d '[:space:]')

if [ "${HAS_USER:-0}" -eq 0 ]; then
  warn "user_subscriptions 没活跃用户, 插入一个测试用户"
  docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -c "
    INSERT INTO user_subscriptions (openid, is_active) VALUES ('e2e_demo_openid_001', true);
  " 2>&1 | head -5
fi

resp=$(curl -sS -X POST http://localhost:8888/api/v1/topics/push \
  -H "X-Admin-Token: $ADMIN_API_KEY")
note "POST /push 响应: $resp"

sleep 8

if docker compose logs consumer-push --tail 50 2>&1 | grep -qE "推送成功|push_success|DLQ"; then
  ok "推送消费者处理了消息 (看 consumer-push 日志细节)"
else
  bad "推送消费者日志没动; 检查:"
  warn "  docker compose ps consumer-push"
  warn "  docker compose logs consumer-push --tail 30"
fi

# ---------------------------------------------------------------------
# 3. 用户选择话题
# ---------------------------------------------------------------------
hr "3. 用户选择话题"

if [ -z "$TOPIC_ID" ]; then
  bad "没有可用 TOPIC_ID, 跳过"
else
  resp=$(curl -sS -X POST http://localhost:8888/api/v1/topics/choice \
    -H "X-Admin-Token: $ADMIN_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"topic_id\": $TOPIC_ID, \"choice_index\": 1, \"user_openid\": \"e2e_demo_openid_001\"}")
  note "POST /choice 响应: $resp"

  CHOICE_ID=$(echo "$resp" | python -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('choice_id',''))" 2>/dev/null)
  if [ -n "$CHOICE_ID" ]; then
    ok "UserChoice 已创建: choice_id=$CHOICE_ID"
  else
    bad "UserChoice 创建失败; 看 topics 日志"
  fi

  # 再选一次 — 应被唯一约束挡 (M1 修复)
  resp2=$(curl -sS -X POST http://localhost:8888/api/v1/topics/choice \
    -H "X-Admin-Token: $ADMIN_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"topic_id\": $TOPIC_ID, \"choice_index\": 1, \"user_openid\": \"e2e_demo_openid_001\"}")
  note "重复选 response: $resp2"
  if echo "$resp2" | grep -q "已选择过该话题"; then
    ok "唯一约束生效 (重复选被挡)"
  else
    warn "重复选没挡; 看 topics 日志 (可能没走 M1 迁移)"
  fi
fi

# ---------------------------------------------------------------------
# 4. 触发文章生成 (DeepSeek)
# ---------------------------------------------------------------------
hr "4. 触发文章生成"

note "调 process_user_choices task (同步等待结果, 最多 60s)"
docker compose exec -T celery-worker \
  celery -A app.tasks.celery_app call app.tasks.article_tasks.process_user_choices 2>&1 | tail -5

sleep 5

ART_COUNT=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
  -c "SELECT COUNT(*) FROM generated_articles WHERE topic_id=$TOPIC_ID;" 2>/dev/null | tr -d '[:space:]')

if [ "${ART_COUNT:-0}" -ge 1 ]; then
  ok "已生成 ${ART_COUNT} 篇文章"
  docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" \
    -c "SELECT id, title, status, length(content) as bytes FROM generated_articles WHERE topic_id=$TOPIC_ID;"
else
  bad "没生成文章; 看 celery-worker --tail"
  warn "可能原因:"
  warn "  - DEEPSEEK_API_KEY 无效 (logger.error 'DeepSeek API错误')"
  warn "  - 网络封锁 api.deepseek.com"
  warn "查日志: docker compose logs celery-worker --tail 50 | grep -E '生成|DeepSeek'"
fi

ARTICLE_ID=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
  -c "SELECT id FROM generated_articles WHERE topic_id=$TOPIC_ID ORDER BY id DESC LIMIT 1;" 2>/dev/null | tr -d '[:space:]')

# ---------------------------------------------------------------------
# 5. 发布到公众号草稿箱
# ---------------------------------------------------------------------
hr "5. 发布到公众号"

if [ -z "$ARTICLE_ID" ]; then
  bad "没 ARTICLE_ID, 跳过发布"
else
  note "调用 publish_article (Celery), article_id=$ARTICLE_ID"

  # 手动通过 Celery 调, 而非 /publish 端点, 看详细日志
  docker compose exec -T celery-worker \
    celery -A app.tasks.celery_app call app.tasks.article_tasks.publish_article --args="[$ARTICLE_ID]" 2>&1 | tail -5

  sleep 15

  STATUS=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
    -c "SELECT status FROM generated_articles WHERE id=$ARTICLE_ID;" 2>/dev/null | tr -d '[:space:]')
  MEDIA=$(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql -h localhost -p "${PG_PORT:-5555}" -U "${PG_USER:-wechat_user}" -d "${PG_DB:-wechat_ai_publisher}" -tA \
    -c "SELECT wechat_media_id FROM generated_articles WHERE id=$ARTICLE_ID;" 2>/dev/null | tr -d '[:space:]')

  if [ "$STATUS" = "published" ] && [ -n "$MEDIA" ]; then
    ok "已发布: status=published, media_id=$MEDIA"
  elif [ "$STATUS" = "draft" ]; then
    warn "status=draft — 草稿创建成功但发布失败, 看 access_token / 网络"
    warn "查日志: docker compose logs celery-worker --tail 50 | grep -E 'publish|发布|access_token'"
  else
    bad "发布失败: status=$STATUS; 看 celery-worker 日志"
  fi
fi

# ---------------------------------------------------------------------
# 6. 推送"已发布"模板消息 (push_consumer 链路)
# ---------------------------------------------------------------------
hr "6. 推送发布的通知给用户"

sleep 5
PUSH_LOG=$(docker compose logs consumer-push --tail 50 2>&1)
if echo "$PUSH_LOG" | grep -qE "推送成功|DLQ"; then
  ok "推送链路已响应 (详情看 consumer-push --tail 50)"
else
  warn "consumer-push 可能没消费到 publish-success 消息"
  warn "  - WECHAT_TEMPLATE_ID_PUBLISH_SUCCESS 是否设了"
  warn "  - 看 consumer-push --tail"
fi

# ---------------------------------------------------------------------
# 收尾
# ---------------------------------------------------------------------
hr "总结"
echo "完整链路日志汇总命令:"
echo "  docker compose logs celery-worker --tail 100"
echo "  docker compose logs consumer-generate --tail 50"
echo "  docker compose logs consumer-push --tail 50"
note "Done."
