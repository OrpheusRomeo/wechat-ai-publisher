#!/bin/bash
# =====================================================================
# scripts/check_publish.sh
# 每日 digest 状态检查 + 运营提示
#
# 用法:
#   bash scripts/check_publish.sh          # 检查今天 (默认)
#   bash scripts/check_publish.sh 2026-07-08  # 检查指定日期
#
# 建议定时 (每天 09:35 跑一遍):
#   09:30 daily_publish_digest (celery beat)
#   09:35 check_publish.sh (cron / 内置调度)
#
# 退出码:
#   0 = 已发布 (status=PUBLISHED)
#   1 = 草稿 (status=DRAFT) 且需运营手动群发
#   2 = 无 digest 文章 (上游任务没跑 / 跑挂了)
#   3 = 数据库不可达
# =====================================================================
set -u
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
set -a; . ./.env; set +a

# shellcheck disable=SC2153
: "${PG_PORT:=5555}"
: "${PG_USER:=wechat_user}"
: "${PG_DB:=wechat_ai_publisher}"
: "${WECHAT_APP_ID:=}"

DATE="${1:-$(date +%F)}"

PSQL=(docker compose exec -T -e "PGPASSWORD=${PG_PASSWORD:-}" postgres psql \
        -h localhost -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tA)

run_sql() { "${PSQL[@]}" -c "$1"; }

# ----- 颜色 -----
RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
ok()   { printf "${GREEN}[PASS]${RESET} %s\n" "$*"; }
bad()  { printf "${RED}[FAIL]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${RESET} %s\n" "$*"; }
note() { printf "${BLUE}[INFO]${RESET} %s\n" "$*"; }

# ----- 1. PG 探活 -----
if ! docker compose exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    bad "Postgres 不可达"
    exit 3
fi

note "日期: $DATE"

# ----- 2. 拉今日所有 digest 文章 -----
ROWS=$(run_sql "SELECT id, title, status, wechat_media_id, error_msg
                FROM generated_articles
                WHERE metadata->>'is_digest' = 'true'
                  AND metadata->>'digest_date' = '$DATE';" 2>&1)
if [ -z "$ROWS" ]; then
    bad "今日 ($DATE) 没有 digest 文章, 上游 daily_publish_digest 没跑或挂了"
    note "排查: docker compose logs celery-worker --tail 50 | grep daily_publish"
    exit 2
fi

# ----- 3. 逐行分析 -----
TOTAL=$(echo "$ROWS" | wc -l | tr -d ' ')
PUBLISHED=0
DRAFT=0
DRAFT_ID=""
DRAFT_MEDIA=""
echo "$ROWS" | while IFS='|' read -r ID TITLE STATUS MEDIA ERR; do
    STATUS=$(echo "$STATUS" | tr -d ' ')
    if [ "$STATUS" = "PUBLISHED" ]; then
        ok "[id=$ID] 已群发"
    else
        warn "[id=$ID] 草稿未群发 (status=$STATUS)"
        [ -n "$MEDIA" ] && note "    media_id=$MEDIA"
        [ -n "$ERR"   ] && note "    原因: $ERR"
    fi
done

# 上面的 `while | pipe` 会创建子 shell, 计数会丢; 在外面再扫一次
while IFS='|' read -r ID TITLE STATUS MEDIA ERR; do
    STATUS=$(echo "$STATUS" | tr -d ' ')
    if [ "$STATUS" = "PUBLISHED" ]; then
        PUBLISHED=$((PUBLISHED + 1))
    else
        DRAFT=$((DRAFT + 1))
        DRAFT_ID="$ID"
        DRAFT_MEDIA="$MEDIA"
    fi
done <<< "$ROWS"

echo
note "今日 digest 总结: ${PUBLISHED} 已发布 / ${DRAFT} 待发布 (共 ${TOTAL} 条)"

# ----- 4. 0 条 digest 且流程未跑过则警告 -----
if [ "$TOTAL" = "0" ]; then
    bad "今日 digest 计数为 0"
    warn "查询今日 celery beat 是否派发了 daily-publish-digest:"
    warn "  docker compose logs celery-beat --since 6h | grep daily-publish"
    exit 2
fi

# ----- 5. 唯一草稿则贴运营操作步骤 -----
if [ "$DRAFT" = "1" ] && [ -n "$DRAFT_ID" ]; then
    echo
    warn "=========================================================="
    warn " [运营操作] 公众号 → 草稿箱 → 找标题为上方的 title"
    warn "           状态: 草稿 → 点 [群发] (订阅号 1 篇/天配额)"
    warn "=========================================================="
    run_sql "SELECT '[运营] 也可走 API 验证:',
                    'GET  /api/v1/articles/{'$DRAFT_ID'}',
                    '字段 wechat_media_id = $DRAFT_MEDIA';" >/dev/null 2>&1
    exit 1
fi

# 全部已发布 OR 当天其实就没 digest (前面已 exit 2)
exit 0
