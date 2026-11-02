# wechat-ai-publisher 流程说明文档

> 编写时间：2026-07-07
> 范围：`wechat-ai-publisher` 当前运行在订阅号上的 AI 自动文章发布系统。

---

## 1. 当前运行状态（采自 docker compose ps）

| 容器 | 服务 | 健康 | 备注 |
|---|---|---|---|
| wechat-app | FastAPI × 4 workers | ✅ healthy | 监听 8888；lifespan 调 `init_db()`（已废弃的空函数，不建表）+ `redis_client`；建表走 Alembic `migrate` 服务 |
| wechat-celery-worker | Celery 4 线程池 | ⚠️ unhealthy | 已加载 6 个 task；35 min 前重启 |
| wechat-celery-beat | 调度器 | ✅ healthy | `/var/lib/celerybeat/celerybeat-schedule` 持久化正常 |
| wechat-consumer-generate | Kafka 文章生成消费 | ⚠️ unhealthy | group=`wechat-consumer-group-generate` |
| wechat-consumer-push | Kafka 客服消息推送消费 | ⚠️ unhealthy | group=`wechat-consumer-group-push` |
| wechat-consumer-publish | Kafka 发布消费 | ⚠️ unhealthy | group=`wechat-consumer-group-publish` |
| wechat-kafka / zookeeper / kafka-ui | Kafka 集群 + UI | ✅ healthy | broker 9092/9093，UI 9090 |
| wechat-postgres | PG 15 | ✅ healthy | 内部 5555 端口，宿主 5555 暴露 |
| wechat-redis | Redis 7.2 | ✅ healthy | 内部 6666 端口，宿主 6666 暴露 |

**接口烟测**：
- `GET /` → 200 `{name: "微信AI文章发布系统", version: "2.0.0", status: running}` ✅
- `GET /health` → 200 `{status: healthy}` ✅
- `GET /api/v1/topics`、`/api/v1/articles` → 307 redirect 到带尾斜杠的版本（缺少 `X-Admin-Token` 头时为 403 而不是 307；目前是路由重定向）

**Celery beat 当前已派发**：
- 每 30 分钟一次 `process-user-choices`（最近几次：12:00、12:30、13:00、13:30 各投递过，0 条待处理）
- 09:30 `daily-publish-digest`（最近一次：07-07 11:45 启动，因为 digest 配额耗尽或草稿长度超限，留 DRAFT，errcode 45004）
- 09:00 `push-topics-to-users`（默认 `ENABLE_WECHAT_PUSH=false` 直接 skip；认证订阅号可手动开；**该任务已弃用**，默认主调度是 09:30 的 digest）
- 按账号动态注入的 `crawl-{account_id}` / `push-{account_id}`（由 `PlatformAccount.crawl_cron` / `push_cron` 决定，`app/tasks/celery_app.py:110-152`）

---

## 2. 架构总览

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   BaiduHot   │    │     LLM      │    │   WeChat     │
│   Crawler    │    │   Client     │    │   Service    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌───────────────────────────────────────────────────────┐
│  PostgreSQL ─ Redis (broker/cache/token/锁) ─ Kafka   │
└───────────────────────────────────────────────────────┘
   ▲         ▲                  ▲              ▲      ▲
   │         │                  │              │      │
   │   Celery tasks (crawl/article/push queue)   │      │
   │         │                  │              │      │
   └─────────┴──────────────────┴──────────────┘      │
              FastAPI (8888)  ──  webhook (/wechat) ───┘
   ▲                                            ▲
   └──── beat (调度)         consumers (3 个) ──┘
                            (generate/push/publish)
```

两条并行的处理路径：
- **Celery 路径** — `app/tasks/*`（通过 Redis broker）
- **Kafka 路径** — `consumers/*`（三条独立 topic + consumer group）

注意：**修改行为时必须两条路径一起改**，否则容易只生效一条。

---

## 3. 状态机

### 3.1 HotTopic
```
PENDING ──(push_topics_to_users)──▶ PUSHED ──(user choice)──▶ SELECTED ──▶ PROCESSED
   │                                   │                          │
   │                                   └───(user choice)──────────┘
   └────────────────────────────(手动)──▶ FAILED (兜底状态，少用)
```

关键约束：`user_choices` 上 `(openid, topic_id)` 唯一约束（迁移 0003 引入）→ 同一用户对同一话题不能重复选。

### 3.2 GeneratedArticle
```
GENERATING ──▶ GENERATED ──▶ PUBLISHING ──▶ PUBLISHED
                                          ├─▶ DRAFT  (草稿保留，create_draft 失败 / publish_draft 配额耗尽)
                                          └─▶ FAILED
```

幂等键：`UniqueConstraint(topic_id, choice_id)` 与 `UserChoice.choice_id` 形成 1:1，避免 Celery/Kafka 双路径并发写入。

---

## 4. 数据流（按时序）

### 4.1 每日 08:00 — 百度热搜爬取

`celery beat` → `app.tasks.crawl_tasks.crawl_hot_topics` → 调 `BaiduHotCrawler`，按 `(title, heat_score)` + 24h 窗口去重后写入 `hot_topics`（状态 `PENDING`）。

手动入口：`POST /api/v1/topics/crawl` 返回 `task_id`，需带 `X-Admin-Token`。

### 4.2 每日 09:00 — 推送话题（订阅号默认关闭）

如果 `ENABLE_WECHAT_PUSH=true`：

```
push_topics_to_users (Celery)
  ├─ 查 PENDING 状态 + 今日创建的话题
  ├─ 查 user_subscriptions 中 is_active AND last_interact_time >= now-48h
  ├─ 仅在 PENDING → PUSHED 推进（不覆盖 SELECTED）
  └─ 给每个用户调 WeChatService.send_custom_message（必须 48h 窗口）
```

订阅号约束：没有模板消息权限，主动通知只能走客服消息 `send_custom_message`；09:00 大多数用户不在 48h 互动窗口，触达近 0；当前 `.env` 设 `ENABLE_WECHAT_PUSH=false`，任务直接 skip 不调微信 API。该任务已弃用，默认主调度是 09:30 的 `daily_publish_digest`。

### 4.3 每日 09:30 — Daily Digest 群发（替代 push）

订阅号的"唯一能覆盖所有粉丝"的渠道是 `freepublish/submit`，配额 `WECHAT_DAILY_PUBLISH_QUOTA` 限制：

```
daily_publish_digest (Celery)
  1. 幂等检查: extra_metadata.digest_date == today → skip
  2. 拉今日 top 10 话题
  3. DailyDigestService.build_digest() 组装 markdown
  4. 配额预检：剩余配额=0 直接落 DRAFT + errcode 备注
  5. 写 PUBLISHING 行，wechat.create_draft + publish_draft
  6. 成功 → PUBLISHED，不发 push 通知（群发本身已触达）
     失败 → DRAFT + error_msg
```

### 4.4 用户互动（实时）

```
微信服务器 ──POST──▶ /webhook/wechat
                       │
                       ├─ event=subscribe  → upsert user_subscriptions
                       │   + 被动回复欢迎语（含今日 top 10 话题）
                       │
                       ├─ event=unsubscribe → is_active=false（不删）
                       │
                       ├─ event=CLICK + EventKey=CHOOSE_TOPIC_<id>
                       │   → 写 UserChoice + 置 topic.status=SELECTED
                       │
                       └─ text 消息 → 回显原文 + 刷新 last_interact_time
```

注意：欢迎语走被动回复（不占 48h 窗口），主动 push 才走客服消息（48h 窗口）。

### 4.5 用户选话题（两个入口）

入口 A — HTTP：
```
POST /api/v1/topics/choice   body: {topic_id, choice_index, user_openid}
   ├─ 写 user_choices（命中唯一约束 → 400）
   └─ topic.status = SELECTED
```

入口 B — 微信菜单（见 4.4 的 CLICK handler），效果一致。

### 4.6 每 30 分钟 — process_user_choices

```
process_user_choices (Celery, queue=article)
  ├─ 查 UserChoice WHERE is_processed = false LIMIT 200
  ├─ KafkaProducerService.send_generate_request({topic_id, choice_id, user_openid})
  └─ flush(5s)
```

注意：**改 Celery 路径与改 Kafka 路径要同时改**。本任务现在只投 Kafka，不再 `.delay()`。

### 4.7 Kafka 文章生成（消费者 A）

```
generate_consumer (订阅 topic=generate-articles, group=*-generate)
  ├─ 校验 topic 存在
  ├─ 查 GeneratedArticle(topic, choice) 复用已有
  ├─ ArticleGenerationService.generate(...) → LLM 客户端 POST /chat/completions（供应商可配置）
  ├─ 落库 GeneratedArticle（status=GENERATED）
  ├─ 标记 UserChoice.is_processed=true
  ├─ send_article_generated → push topic（push_consumer 处理）
  └─ 如果 enable_auto_publish → send_publish_request → publish topic
```

`process_message` 失败重试：超过 `kafka_consumer_max_attempts` 次后转 DLQ（`consumers/_dlq.py`）。

### 4.8 Kafka 发布（消费者 B）

```
publish_consumer (订阅 topic=publish-articles, group=*-publish)
  ├─ Redis 锁 wechat:lock:publish_article:<id> (与 Celery publish_article 共用)
  ├─ SELECT FOR UPDATE + 状态早退
  ├─ 配额预检（已达上限 → DRAFT）
  ├─ wechat.create_draft → media_id
  ├─ wechat.publish_draft → errcode=0 ?
  │     ├─ 0 → status=PUBLISHED + publish_id + 推送 publish-success
  │     ├─ 45009/45004 → DRAFT + error_msg（不重试）
  │     └─ 其它 → DRAFT + error_msg
  └─ release_lock
```

注意：同样的 publish 请求可能来自 Celery 路径（`POST /api/v1/articles/{id}/publish`）或 Kafka 路径，两条路径都拿同一把 Redis 锁 → 不并发推到微信。

### 4.9 Kafka 推送（消费者 C）

```
push_consumer (订阅 topic=push-notifications, group=*-push)
  ├─ 校验消息字段
  ├─ wechat_enable_custom_message=false → 记 skipped_disabled + return
  ├─ _user_in_48h_window(user_openid)?
  │     └─ false → 记 skipped_48h_window + return
  ├─ publish_status=published? → "已发布" 文案；else → "草稿已生成" 文案
  ├─ wechat.send_custom_message
  │     ├─ true → push_success
  │     ├─ 48001 (未认证订阅号无客服消息权限) → 仅 INFO 一次
  │     └─ 其它 → 错误日志
```

事件 `push_skipped_disabled`、`push_skipped_48h_window`、`push_success` 都回到 `kafka_topic_push` 便于后续接入统计（自循环）。

---

## 5. 关键设计点

### 5.1 防并发（同一 article_id）
- Celery 路径与 Kafka 路径的 publish 共用同一把 Redis 锁：`wechat:lock:publish_article:<id>`。
- `publish_article` 内还做 `SELECT ... FOR UPDATE` 二次校验，防锁过期窗口的并发。
- 已 `PUBLISHED`/`PUBLISHING` 状态早退，不重复调微信 API。

### 5.2 幂等（同 topic + choice 不重复）
- `user_choices` 表：`UniqueConstraint(openid, topic_id)` — M1 修复
- `generated_articles` 表：`UniqueConstraint(topic_id, choice_id)` — #4 修复
- 并发竞态：捕获 `IntegrityError` 后回滚重查复用，绝不重复调 LLM。

### 5.3 失败可恢复（DLQ）
- `consumers/_dlq.py` 计数失败次数，超过阈值转 DLQ + commit offset。
- Kafka 消费者关闭 auto.offset.store，由业务处理成功后手动 commit；失败未超阈值时不 commit，下一轮 poll 重试。

### 5.4 订阅号专属约束
| 限制 | 应对 |
|---|---|
| 无模板消息权限 | 主动通知全部走 `send_custom_message`（客服消息），受 48h 互动窗口限制 |
| 无 `snsapi_base` | 本项目暂无网页授权，未涉及 |
| 每日群发配额 | `WECHAT_DAILY_PUBLISH_QUOTA` 默认 1；`publish_article` 与 `daily_publish_digest` 都先预检 |
| 客服消息 48h 窗口 | `push_tasks` SQL 预过滤；`push_consumer` 单条再查；`webhook` 在 subscribe/click/text 三处刷新 `last_interact_time` |
| `draft/add` 必须 `thumb_media_id` | `_get_or_upload_default_cover()` 上传一张 900×383 图到永久素材并 Redis 缓存 |
| 字段硬限制 | title ≤ 17 字符、author ≤ 8 字节；越界自动截断 |

---

## 6. 入口速查表

| 想做什么 | 改哪里 |
|---|---|
| 调爬取 | `POST /api/v1/topics/crawl` |
| 调推送 | `POST /api/v1/topics/push` |
| 用户选话题（HTTP） | `POST /api/v1/topics/choice` |
| 用户选话题（微信菜单） | 配菜单 EventKey `CHOOSE_TOPIC_<id>`，走 webhook |
| 生成文章（同步预览） | `POST /api/v1/articles/preview` |
| 生成文章（异步任务） | `POST /api/v1/articles/generate` |
| 立刻发布文章 | `POST /api/v1/articles/{id}/publish` |
| 看一下今日话题 | `GET /api/v1/topics/today?limit=N` |
| 手动重投生成请求 | `docker compose exec celery-worker celery -A app.tasks.celery_app call app.tasks.article_tasks.process_user_choices` |

---

## 7. 已运行的脚本（用于验证）

| 脚本 | 用途 |
|---|---|
| `scripts/run_flow.sh` | 一键完整流程（爬→推→选→成→发） |
| `scripts/e2e_verify.sh` | 端到端冒烟（带 PASS/FAIL 标记） |
| `scripts/smoke_test.sh` | 看现状（Kafka group lag + DB tables + consumer logs） |
| `scripts/status_check.sh` | 服务连通性、关键路径检查 |
| `scripts/post_rebuild_check.sh` | 重建后健康检查 |
| `scripts/trigger_crawl.sh` | 单独触发一次爬取 |

---

## 8. 后续建议

1. **09:30 digest 当前总走草稿**：日志见 `wechat-celery-worker` `errcode=45004 description size out of limit` —— 标题或内容超阈值被拒；可考虑：
   - 临时关闭 `daily_publish_digest` 直到官方配额季末；
   - 或在 `DailyDigestService.build_digest` 内把 markdown 整体控制在阈值之内（实测 description 上限仅 200 字左右）。
2. **观察 48h 推送窗口**：订阅号默认触达近 0，可在测试阶段手动 `UPDATE user_subscriptions SET last_interact_time=NOW()` 验证主路径。
3. **`AUTO_PUBLISH` 与 digest 共用同一个 1 篇/天配额**：09:30 digest 用完额度后，所有 `process_user_choices` 触发的自动发布都会回落到 DRAFT，运营侧需在公众号后台手工群发。可考虑把 digest 时段挪到 06:00 与日常文章错开，或支持可配置的 `digest_priority`。
4. **修一处启动日志噪音**：consumer-print 的 `_partition_eof` 在 INFO 级别刷屏（CPython `KafkaConsumer` 默认行为），可在 `consumer.config` 加 `enable.partition.eof=false`。
