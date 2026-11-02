# wechat-ai-publisher — 技术流程文档

> 适用版本：`app_name=微信AI文章发布系统` `version=2.0.0`
> 文档基线：2026-07-08
> 阅读对象：新加入项目的工程师 / 负责运维接手的同学

---

## 0. 一句话概括

每天 08:00 自动爬取百度热搜 → 09:30 LLM 给每条热搜生成 30-40 字简讯、由 `daily_publish_digest` 把 top 10 头条话题拼成一篇「今日热搜早报」文章推到公众号草稿箱。运营同学 09:35 在草稿箱点一下「群发」，就完成了当天的内容分发。中间用户互动走微信公众号回调（关注/收消息/点菜单），可以选择感兴趣的话题让系统按选择生成深度文章。

---

## 1. 技术栈

| 层 | 选型 | 备注 |
|---|---|---|
| Web 框架 | FastAPI 0.104 | `app/main.py`，`/api/v1/*` + `/webhook/*` |
| ORM | SQLAlchemy 2.0 | 同步 `create_engine` + `QueuePool`，**不是** 异步池 |
| 数据库 | PostgreSQL 15 (JSONB + 枚举) | Alembic 迁移管理 |
| 缓存 / 队列 | Redis 7.2 | DB 0 缓存 / DB 1 celery broker / DB 2 backend |
| 消息总线 | Kafka (confluent-kafka 2.5, librdkafka) | 5 个 topic：crawl / generate / push / publish / dlq |
| 任务队列 | Celery 5.3 (线程池, concurrency=4) | 3 个 queue：crawl / article / push |
| AI | LLM 供应商可配置（`deepseek` / `openai` / `minimax` / `qwen`） | 模型名取自 `llm_providers` 表或 `DEEPSEEK_MODEL`（默认 `deepseek-chat`）；长文 + 短简讯 |
| 微信 | 公众平台 API (`requests`) | **订阅号**，所有方法都是**同步**调用 |
| 部署 | docker compose | 一次性 `migrate` 服务先跑 Alembic |
| 配置 | Pydantic Settings | `.env` 由 .gitignore 隔离 |
| 日志 | loguru | 终端 + `/app/logs/*.log` |
| 鉴权 | HTTP Header `X-Admin-Token` | 路由级 RequireAdmin |
| 限流 | slowapi | 60/min 列表、10/min LLM 同步调用 |
| 死信 | 自实现 `_dlq` 计数 → 转 DLQ | **进程内内存字典**计数失败次数（非 Redis 全局计数） |

---

## 2. 总体架构

```
                 ┌────────────────────────────────────────────────────────┐
                 │              docker compose (单机编排)                  │
                 │                                                        │
                 │   ┌────────────┐   ┌──────────┐   ┌──────────────┐      │
                 │   │   app      │   │  celery  │   │  celery-beat │      │
                 │   │ FastAPI×4  │   │  worker  │   │  scheduler   │      │
                 │   │  :8888     │   │ 3 queues │   │  :8888       │      │
                 │   └─────┬──────┘   └────┬─────┘   └──────┬───────┘      │
                 │         │              │                │              │
                 │   ┌─────▼──────────────▼────────────────▼─────┐        │
                 │   │     PostgreSQL 5555 | Redis 6666           │        │
                 │   │     (DB: app / celery / cache)             │        │
                 │   └─────┬──────────────────────────────────────┘        │
                 │         │       ▲                                       │
                 │   ┌─────▼───────┴──────┐                                │
                 │   │ Kafka (9092/9093)  │  4 topic:                       │
                 │   │ consumer-generate │  ┌─ generate-articles            │
                 │   │ consumer-publish  │  ├─ publish-articles             │
                 │   │ consumer-push     │  ├─ push-notifications           │
                 │   └───────────────────┘  └─ crawl-topics (备用)          │
                 │                                                        │
                 └─────────────▲──────────────────────────▲─────────────────┘
                               │                          │
                  POST /api/v1│                          │LLM / 微信
                               │                          │HTTPS 出公网
                ┌──────────────┴───────┐         ┌───────┴──────────┐
                │   运营 (浏览器)      │         │  百度热搜        │
                │   /docs Swagger      │         │  LLM API         │
                └──────────────────────┘         │  api.weixin.qq.com│
                                                 └──────────────────┘
```

### 2.1 两条并行的处理路径

业务流程有**两套独立的实现**：**Celery 路径**（`app/tasks/*`）和 **Kafka 路径**（`consumers/*`）。它们各自能完成「生成文章」「通知用户」「发布文章」的功能 —— 修改行为时必须确认是哪条路径被触发，否则只改一处会出现"看似生效实则遗漏"。

| 行为 | Celery 路径 | Kafka 路径 |
|---|---|---|
| 调度入口 | `celery beat` 定时 + `POST /api/v1/.../publish` 调 `.delay()` | `KafkaConsumer.poll()` 监听 topic |
| 启动命令 | `celery -A app.tasks.celery_app worker -Q crawl,article,push` | `python -m consumers.{generate,publish,push}_consumer` |
| 触发 | `crawl_hot_topics.delay()` 等 | Kafka message |
| 失败处理 | `self.retry(exc=, countdown=)` | 不 commit offset → 下次 poll 重试；超阈值转 DLQ |
| 当前默认 | 是 | 是（默认双活）|

两条路径走同一把 Redis 锁 `wechat:lock:publish_article:<id>`，所以同一 article 不会并发推到公众号。

### 2.2 端口约定

| 服务 | 容器内端口 | 宿主机端口 | .env 字段 |
|---|---|---|---|
| FastAPI | 8888 | 8888 | `APP_PORT` |
| Postgres | 5555 | 5555 | `PG_PORT` |
| Redis | 6666 | 6666 | `REDIS_PORT` |
| Kafka | 9093 (internal), 9092 (ext) | 9092, 9093 | `KAFKA_INTERNAL_PORT` |
| Kafka UI | 8080 | 9090 | `KAFKA_UI_PORT` |

---

## 3. 数据模型

### 3.1 ER 概览

```
   ┌──────────────┐ 1     n ┌──────────────┐ 1     n ┌──────────────────┐
   │  hot_topics  │─────────│ user_choices │         │ generated_articles│
   │  (热搜)      │         │ (用户选)     │         │ (生成的文章)     │
   └──────┬───────┘         └──────┬───────┘         └────────┬─────────┘
          │ (topic_id FK)         │ (choice_id 逻辑 FK)     │
          └──────────────────────┴──────────────────────────┘
                                  n:1  →  user_subscriptions
                                  (openid)

  ┌──────────────────┐
  │ user_subscriptions│   last_interact_time  ← webhook (subscribe / CLICK / text)
  │ (公众号订阅用户)  │   is_active
  └──────────────────┘
```

### 3.2 表与字段

#### `hot_topics` — 热搜话题
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `topic_id` | UUID | 业务 ID，唯一 |
| `title` | varchar(200) | |
| `heat_score` | float | |
| `source_url` | varchar(500) | 百度 API `rawUrl` |
| `status` | enum(`PENDING`/`PUSHED`/`SELECTED`/`PROCESSED`/`FAILED`) | |
| `extra_metadata` | JSONB → 列名 `metadata` | 含 `img_url`（百度热搜配图） |
| `push_time` | timestamp | |

> 列名是 `metadata`（PG）但 ORM 叫 `extra_metadata`，避开 SQLAlchemy `Base.metadata` 冲突。

#### `user_choices` — 用户选择记录
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `choice_id` | UUID | 业务 ID，唯一 |
| `user_openid` | varchar(100) | |
| `topic_id` | int FK→hot_topics(id) ON DELETE CASCADE | |
| `choice_index` | int | 用户选的序号 |
| `is_processed` | bool | false → 30 分钟一次调度转 Kafka |
| `process_time` | timestamp | |

**唯一约束**：`UNIQUE (user_openid, topic_id)`（迁移 0003 加）→ 同一用户对同一话题不能重复选。

#### `generated_articles` — 生成的文章
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `article_id` | UUID | 业务 ID，唯一 |
| `topic_id` | int FK→hot_topics(id) ON DELETE CASCADE | |
| `choice_id` | UUID NULL | 与 `user_choices.choice_id` 1:1 |
| `user_openid` | varchar(100) NULL | 发布成功时给此用户发客服消息 |
| `title` | varchar(200) ≤17 字符 | 微信硬上限 |
| `content` | text | Markdown → `_markdown_to_html` 转 HTML |
| `summary` | text | |
| `cover_url` | varchar(500) | 外链；上传到微信 → `thumb_media_id` |
| `tags` | JSONB | |
| `status` | enum(`GENERATING`/`GENERATED`/`PUBLISHING`/`PUBLISHED`/`DRAFT`/`FAILED`) | |
| `wechat_media_id` | varchar | 草稿 media_id |
| `wechat_publish_id` | varchar | 群发 publish_id |
| `extra_metadata` | JSONB | `is_digest=true, digest_date=YYYY-MM-DD, cover_img_url=` |
| `publish_time` | timestamp | |

**唯一约束**：`UNIQUE (topic_id, choice_id)`（迁移 0002 加）→ Celery + Kafka 双路径并发不会重复入库，命中则复用已有文章。

#### `user_subscriptions` — 公众号订阅用户
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 自增主键 |
| `user_id` | UUID NOT NULL | model default=`uuid.uuid4`（INSERT 时要给），unique |
| `openid` | varchar(100) NOT NULL | 普通列 + `UniqueConstraint(user_subscriptions_openid_key)` + 唯一索引 `ix_user_subscriptions_openid` |
| `unionid` | varchar(100) | indexed |
| `nickname` | varchar(100) | |
| `is_active` | bool | |
| `subscribe_time` | timestamp | 由 webhook 写入 |
| `last_interact_time` | timestamp | **48h 客服消息窗口的判定字段** |

索引:`(is_active, last_interact_time)`

### 3.3 状态机

#### HotTopic
```
PENDING ─ push_topics_to_users ─▶ PUSHED ─ user choice ─▶ SELECTED ──▶ PROCESSED
   │                               │                          │
   └───────── (失败/手工) ─────────┴──────────── (失败/手工) ┴─▶ FAILED
```
注意：`PUSHED → SELECTED` 与 `PENDING → SELECTED` 都是合法转移；SELECTED 不被覆盖。

#### GeneratedArticle
```
GENERATING ─ LLM ─▶ GENERATED ─ 调微信 ─▶ PUBLISHING ─▶ PUBLISHED
                                              ├─▶ DRAFT (草稿保留)
                                              └─▶ FAILED
```

转换规则：
- `create_draft` 失败（errcode=40007 invalid media_id / 5xx）→ DRAFT + error_msg
- `publish_draft` 失败（errcode=45009 daily quota / 45004 24h limit）→ DRAFT + error_msg（**不重试**）
- `publish_draft` 失败（errcode=48001 api unauthorized）→ DRAFT + error_msg（个人订阅号无群发权限，永久不重试）

### 3.4 幂等键和去重

| 维度 | 键 | 行为 |
|---|---|---|
| 同 (user, topic) 选 | `UNIQUE(openid, topic_id)` on user_choices | IntegrityError → HTTP 400「已选择过该话题」|
| 同 (topic, choice) 生成文章 | `UNIQUE(topic_id, choice_id)` on generated_articles | IntegrityError → 复用已有 article_id，不重复调 LLM |
| 同 digest 一天一篇 | `extra_metadata.digest_date = YYYY-MM-DD` | 二次跑直接 skip |
| 同 article 多次 publish | Redis 锁 + `SELECT FOR UPDATE` + 状态早退 | 锁不到 → skip；状态非 DRAFT → skip |

---

## 4. 关键代码位置

| 用途 | 文件 | 入口函数 |
|---|---|---|
| FastAPI 启动 | `app/main.py` | `lifespan` 调 `init_db()`（已废弃的空函数，不建表；建表走 Alembic `migrate` 服务）+ `redis_client` + `apply_runtime_overrides()`（管理后台配置→运行时 settings） |
| Celery 实例 | `app/tasks/celery_app.py` | `_build_beat_schedule()`，队列路由 `task_routes` |
| 爬虫 | `app/services/crawler.py` | `BaiduHotCrawler.crawl()` 调百度 `top.baidu.com/api/board` |
| 调 LLM | `app/services/llm_clients/` | `generate_article()` (1500-2000 字) + `generate_brief()` (30-40 字)，供应商可配置 |
| 调微信 | `app/services/wechat_core.py` | `get_access_token()` / `send_customer_message()` / `create_draft()` / `publish_draft()`（唯一实现，两个适配器共用）|
| 微信发布流程 | `app/services/wechat_publish.py` | `publish_wechat_draft()`（建草稿→群发）/ `resolve_cover_url()` / `check_daily_quota()` |
| 拼装 digest | `app/services/daily_digest.py` | `DailyDigestService.build_digest(topics, briefs=)` |
| 话题入 Kafka | `app/services/kafka_producer.py` | `send_generate_request()` / `send_publish_request()` |
| 每日爬 | `app/tasks/crawl_tasks.py` | `crawl_hot_topics` |
| 每日推送 | `app/tasks/push_tasks.py` | `push_topics_to_users` |
| 处理用户选 | `app/tasks/article_tasks.py` | `process_user_choices` (30 min) |
| 单篇生成 | `app/tasks/article_tasks.py` | `generate_article` / `publish_article` |
| 每日 digest | `app/tasks/article_tasks.py` | `daily_publish_digest` |
| Kafka 生成消费 | `consumers/generate_consumer.py` | `GenerateConsumer.start()` |
| Kafka 发布消费 | `consumers/publish_consumer.py` | `PublishConsumer.start()` |
| Kafka 推送消费 | `consumers/push_consumer.py` | `PushConsumer.start()` |
| 用户 webhook | `app/api/v1/webhook.py` | `POST /webhook/wechat` |
| 话题接口 | `app/api/v1/topics.py` | router 强制 `X-Admin-Token` |
| 文章接口 | `app/api/v1/articles.py` | router 强制 `X-Admin-Token` |
| 鉴权 | `app/core/auth.py` | `require_admin` Depends |
| 限流 | `app/core/rate_limit.py` | `limiter` 全局 slowapi |
| Redis | `app/core/redis_client.py` | 同步访问：`redis_client.run_sync(...)` |
| 死信 | `consumers/_dlq.py` | 进程内内存字典计数失败次数 |

### 4.1 Celery Beat 调度表

`app/tasks/celery_app.py:82-103` 的 `_build_beat_schedule()` 生成：

| beat 名称 | 任务 | 时间 | 队列 |
|---|---|---|---|
| `crawl-hot-topics` | `app.tasks.crawl_tasks.crawl_hot_topics` | `SCHEDULER_CRAWL_TIME`（默认 08:00） | `crawl` |
| `push-topics-to-users` | `app.tasks.push_tasks.push_topics_to_users` | `SCHEDULER_PUSH_TIME`（默认 09:00，**已弃用**，见 5.8） | `push` |
| `daily-publish-digest` | `app.tasks.article_tasks.daily_publish_digest` | 09:30 | `article` |
| `process-user-choices` | `app.tasks.article_tasks.process_user_choices` | 每 30 分钟 | `article` |
| `crawl-{account_id}` | `app.tasks.crawl_tasks.crawl_for_account` | 由 `PlatformAccount.crawl_cron` 决定 | `crawl` |
| `push-{account_id}` | `app.tasks.push_tasks.push_for_account` | 由 `PlatformAccount.push_cron` 决定 | `push` |

后两项是 `_add_per_account_schedules()`（`app/tasks/celery_app.py:110-152`）在启动时从 `platform_accounts` 表读 `crawl_cron` / `push_cron` **动态注入**的；账号增删改后可调 `POST /api/v1/tasks/celery/reload-beat` 重新加载。

---

## 5. 完整业务流程（按时序）

### 5.1 每日 08:00 — 爬热搜

```
celery beat  ── cron(hh=08, mm=00) ──▶  app.tasks.crawl_tasks.crawl_hot_topics
                                              │
                                              ▼
                              BaiduHotCrawler.crawl()
   https://top.baidu.com/api/board?platform=pc&tab=realtime
   解析 cards[0].content[]，最多取 20 条
   每条: title, heat_score, source_url, img_url  ← 百度原图 URL
                                              │
                                              ▼
              24h 内同 (title, heat_score) 视为重复 → 跳过
              其余 INSERT hot_topics(status=PENDING, extra_metadata.img_url=)
                                              │
                                              ▼
                                    返回 {saved, skipped, total}
```

手动入口：`POST /api/v1/topics/crawl`（X-Admin-Token 鉴权）→ `.delay()` 把任务丢 Celery。

### 5.2 用户关注公众号（实时）

```
微信服务器 ──POST /webhook/wechat (XML) ──▶  app/api/v1/webhook.py
                                              │
                                              │  event=subscribe
                                              ▼
                              _handle_subscribe(db, openid)
                                ├─ upsert user_subscriptions(openid, is_active=True)
                                ├─ _touch_interact(openid)  ← last_interact_time=NOW()
                                └─ _build_welcome_message(top_10_today)
                                              │
                                              ▼
                              被动回复 XML (含今日 top 10 话题)
                              ↑ 不需要客服消息 48h 窗口
```

`subscribe` / 任何 text 消息 / `event=CLICK` 都会 `_touch_interact()`，让 `last_interact_time` 滚动到当前，避免客服消息窗口永远基于 `subscribe_time`、错失推送机会。

### 5.3 用户选话题（两个入口 + 一个 timer）

入口 A — HTTP：
```
POST /api/v1/topics/choice
  body: {topic_id, choice_index, user_openid}
    │
    ├─ INSERT user_choices (TopicStatus.PUSHED/PENDING → SELECTED)
    ├─ IntegrityError (重复选同一 topic) → HTTP 400
    └─ 200 { choice_id }

入口 B — 微信公众号菜单 CLICK:
  用户点击菜单 → event=CLICK, EventKey=CHOOSE_TOPIC_<id>
    │
    └─ webhook.py:_handle_click_event
         ├─ 同上 INSERT user_choices
         ├─ 命中唯一约束 → 静默忽略
         └─ 命中: topic.status ← SELECTED

Buffer — 每 30 分钟 (celery beat):
  process_user_choices
    │
    ├─ SELECT user_choices WHERE is_processed = false LIMIT 200
    ├─ KafkaProducerService.send_generate_request({topic_id, choice_id, user_openid})
    └─ flush(5s)  确保 broker 收到再提交事务
```

> **注意**：`process_user_choices` 不再 `.delay()` 调 `generate_article`，只投 Kafka；Celery 端 `generate_article` 主要用于 `POST /api/v1/articles/generate` 同步触发。

### 5.4 Kafka 文章生成（消费者 A）

```
GenerateConsumer  ─ 订阅 generate-articles, group=*-generate
  poll(timeout=1s)
    │
    ├─ JSON decode 失败 → DLQ + commit
    ├─ process_message(data)
    │     │
    │     ├─ SELECT topic (not found? log + return 成功 → commit)
    │     ├─ 同一 (topic, choice) 已存在 → 复用 + 发 push topic → return
    │     │
    │     ├─ ArticleGenerationService.generate(...) → LLM 客户端
    │     │   POST /v1/chat/completions
    │     │   system: 你是一位资深新闻评论员...
    │     │   user:   _build_prompt(title)  ← 1500-2000 字 + 四段式
    │     │   returns: {title, content, cover_description}
    │     │
    │     ├─ INSERT generated_articles(status=GENERATED)
    │     │     IntegrityError → 回滚 + 复用已有 article (路径见 #4 修复)
    │     ├─ user_choice.is_processed = true
    │     │
    │     ├─ send_article_generated({article_id, ..., user_openid})
    │     │   → publish-notifications topic (push_consumer 负责发客服消息)
    │     │
    │     └─ if ENABLE_AUTO_PUBLISH:
    │         send_publish_request({article_id, publish_type="auto"})
    │           → publish-articles topic (publish_consumer 负责发草稿+群发)
    │
    ├─ 持续失败 → _dlq.record_failure 计数 → 超 max_attempts → DLQ + commit
    └─ 成功 → commit offset + _dlq.cleanup
```

### 5.5 公众号文本消息互动（实时）

```
微信服务器 ──POST /webhook/wechat (text 消息) ──▶  webhook.py
                                          │
                                          ├─ _touch_interact(openid)
                                          ├─ 回显原文 (passive reply)
                                          └─ 200 OK + XML
```

### 5.6 Kafka 发布（消费者 B）

```
PublishConsumer  ─ 订阅 publish-articles, group=*-publish
  │
  ├─ Redis 锁 wechat:lock:publish_article:<id> (与 Celery publish_article 共用)
  ├─ 取不到锁 → log warning + return 成功 (commit offset)
  │
  ├─ SELECT FOR UPDATE generated_articles.id
  ├─ status 是 PUBLISHED / PUBLISHING → 早退 (避免重复群发)
  ├─ 当日已发布 >= WECHAT_DAILY_PUBLISH_QUOTA → status=DRAFT + error_msg
  │
  ├─ article.status ← PUBLISHING
  ├─ wechat.create_draft(title, content_md, cover_url=..., digest=summary)
  │     ├─ _get_or_upload_default_cover (无 cover_url 时的回退)
  │     │     查 Redis wechat:default_cover_media_id
  │     │     miss → 下载 WECHAT_DEFAULT_COVER_URL → 上传 permanent material
  │     │            缓存 media_id 到 Redis (永久)
  │     └─ 或 _upload_cover_from_url(cover_url)
  │           按 URL SHA1 前 16 位做 Redis key
  │           miss → 下载 → 上传 permanent material
  │
  ├─ article.wechat_media_id ← media_id
  ├─ wechat.publish_draft(media_id) → {errcode, errmsg, publish_id}
  │     ├─ errcode=0 → status=PUBLISHED, publish_time, wechat_publish_id
  │     │             └─ _send_publish_success_notification → push topic
  │     ├─ errcode in {45009, 45004} → DRAFT + error_msg (不重试)
  │     └─ 其它 → DRAFT + error_msg
  │
  └─ release_lock
```

`create_draft` 已修过两个核心 bug：
1. **JSON 中文 `\uXXXX`**：用 `json.dumps(..., ensure_ascii=False)` 发原生 UTF-8（`_post_json`）
2. **缺 `thumb_media_id`**：永远必填，传了 `cover_url` 就上传外部图，否则用默认图

### 5.7 Kafka 推送（消费者 C）

```
PushConsumer  ─ 订阅 push-notifications, group=*-push
  │
  ├─ 消息必填 user_openid + article_title
  ├─ WEICHAT_ENABLE_CUSTOM_MESSAGE=false → system_event(push_skipped_disabled) + return
  ├─ _user_in_48h_window(user_openid)?
  │     ├─ false → system_event(push_skipped_48h_window) + return
  │     └─ true  ↓
  │
  ├─ content = (publish_status=published ? "已发布" 模板 : "草稿已生成" 模板)
  ├─ wechat.send_custom_message(openid, content)
  │     ├─ 48001 → log info "未认证无客服消息权限" + return
  │     ├─ 45015 → log error "用户不在 48h 窗口" (推送前已预检过, 应该不会到这里)
  │     ├─ 40001/42001/... → 强制刷新 token 重试一次
  │     └─ true → system_event(push_success)
  └─ commit offset
```

订阅号**没有**模板消息 (`template/send`); 唯一主动通知方式就是客服消息。

### 5.8 每日 09:00 — 推送话题（**已弃用**，订阅号默认关闭）

```
celery beat ── cron(hh=09, mm=00) ──▶  push_topics_to_users
  │
  └─ if ENABLE_WECHAT_PUSH=true:
       ├─ SELECT hot_topics WHERE status=PENDING AND create_time>=today
       │                       ORDER BY heat_score DESC LIMIT 10
       ├─ SELECT user_subscriptions WHERE is_active=true
       │                       AND last_interact_time >= now-48h  ← SQL 预检
       ├─ 对每个 (topic, user) 调 send_custom_message
       └─ PENDING → PUSHED (但不覆盖 SELECTED)
     else:
       log.info("微信推送开关关闭")  ← 本项目当前默认
```

> 该任务已弃用：订阅号没有模板消息权限，只能用客服消息，而 09:00 绝大多数用户不在 48h 互动窗口内，触达率近 0。代码保留供 `enable_wechat_push=true` 的认证订阅号复用；**默认主调度是 09:30 的 `daily_publish_digest`**（见 5.9），走群发覆盖所有粉丝。

### 5.9 每日 09:30 — 热搜早报（核心流程，本项目最有价值的一段）

```
celery beat ── cron(hh=9, mm=30) ──▶  daily_publish_digest
  │
  ├─ 1. 幂等检查
  │     SELECT generated_articles WHERE extra_metadata.digest_date = today
  │     → 如果存在 → skip + return
  │
  ├─ 2. 拉今日 top 10 话题
  │     SELECT hot_topics WHERE create_time >= today
  │                       ORDER BY heat_score DESC LIMIT 10
  │
  ├─ 3. LLM 给每条生成 30-40 字简讯
  │     for t in topics:
  │        briefs[t.id] = llm_client.generate_brief(t.title)   ← 关键修改
  │     失败回退 = topic.title
  │
  ├─ 4. DailyDigestService.build_digest(topics, briefs)
  │     返回 (title, content_md, summary, count, cover_img_url)
  │     cover_img_url ← topics[0].extra_metadata.img_url
  │
  │     内容格式:
  │     ┌──────────────────────────────────┐
  │     │ 今日 7-8 百度热搜 TOP 10         │
  │     │                                  │
  │     │ 1 # 中方回应多国"批评"中国试射导弹│
  │     │   <LLM 简讯 30-40 字>           │
  │     │   https://www.baidu.com/s?wd=... │
  │     │                                  │
  │     │ 2 # 广西横州急需蛇毒血清         │
  │     │   ...                            │
  │     └──────────────────────────────────┘
  │
  ├─ 5. 配额预检
  │     today_published = SELECT COUNT ... WHERE status=PUBLISHED AND publish_time>=today
  │     if today_published >= WECHAT_DAILY_PUBLISH_QUOTA:
  │         status=DRAFT + error_msg="今日已发布 N/Q 篇"
  │
  ├─ 6. INSERT generated_articles(status=PUBLISHING,
  │                                extra_metadata={is_digest, digest_date, cover_img_url})
  │     cover_url = cover_img_url    ← 写库供溯源
  │
  ├─ 7. 微信
  │     draft = wechat.create_draft(title, content_md,
  │                                 cover_url=cover_img_url,
  │                                 author=settings.app_name,
  │                                 digest=summary)         ← ensure_ascii=False
  │     │
  │     ├─ draft 为空 → status=DRAFT, error_msg="创建草稿失败"
  │     └─ OK:
  │         article.wechat_media_id ← media_id
  │         result = wechat.publish_draft(media_id)
  │           ├─ errcode=0 → status=PUBLISHED, publish_time, wechat_publish_id
  │           ├─ 45009 / 45004 → status=DRAFT + error_msg (不重试)
  │           └─ 48001 (个人订阅号无群发) → status=DRAFT + error_msg
  │
  └─ DONE. 运营去草稿箱看 "7-8 日 热搜早报" 然后群发。
```

### 5.10 每日 09:35 — 运营检查提醒

```
$ bash scripts/check_publish.sh          # 见 scripts/check_publish.sh

1. 看 status=PUBLISHED → ok
2. 看 status=DRAFT + wechat_media_id != null →
   提示运营「去公众号后台 → 草稿箱 → 群发」
3. 不存在 → 提示排查 daily_publish_digest 为什么没跑
```

---

## 6. HTTP 接口一览

### 6.1 全部需要 `X-Admin-Token` 头（除 webhook 外）

#### `topics` (`/api/v1/topics/`)
| Method | Path | 说明 |
|---|---|---|
| GET | `/` | 分页列表，支持 `?status=` `?category=` `?page=` `?page_size=` |
| GET | `/today` | 今日 PENDING，按热度倒序 |
| GET | `/{id}` | 详情 |
| POST | `/` | 手工创建话题 |
| PUT | `/{id}` | 改任意字段 |
| PATCH | `/{id}/status` | 改 status |
| POST | `/crawl` | 触发爬取 (返回 celery task_id) |
| POST | `/push` | 触发推送 |
| POST | `/choice` | 用户选话题 (body: topic_id, choice_index, user_openid) |

#### `articles` (`/api/v1/articles/`)
| Method | Path | 说明 |
|---|---|---|
| GET | `/` | 分页列表，`?status=` `?topic_id=` |
| GET | `/{id}` | 详情 |
| POST | `/` | 手工创建文章 |
| PUT | `/{id}` | 改任意字段（`exclude_unset=True` 防止 None 吞字段） |
| POST | `/generate` | 异步生成 (返回 celery task_id)，`?topic_id, ?choice_id, ?user_openid, ?custom_prompt` |
| POST | `/preview` | 同步生成预览 (5/min 限流)，`?topic_title, ?style=` |
| POST | `/{id}/publish` | 发布，body `{publish_type}` |
| PATCH | `/{id}/status` | 改 status + error_msg |

#### `webhook` (`/webhook/wechat`) — 不需要 admin token
| Method | Path | 说明 |
|---|---|---|
| GET | `/wechat` | 微信 URL 校验，4 参数 `signature/timestamp/nonce/echostr` |
| POST | `/wechat` | 消息/事件回调 (subscribe/unsubscribe/CLICK/text) |
| GET | `/health` | `/webhook/health` |

### 6.2 关键设计点

| 设计 | 原因 |
|---|---|
| 路由级 `dependencies=[Depends(require_admin)]` | 防止整组接口被爆破；admin token 比 URL 密钥更易轮换 |
| `slowapi` 限流：列表 60/min、generate 10/min、preview 5/min | 防 LLM 同步调用爆账号配额 |
| `POST /api/v1/articles/generate` 先 404 校验 topic 存在 | 修 #M9 bug：原来 200 + Celery 内部 ValueError |
| `PUT /articles/{id}` 用 `exclude_unset=True` | 修 #L10 bug：原来 `if value is not None` 会吞显式 null |

### 6.3 限流

- 60 / minute - 默认所有限流接口
- 10 / minute - LLM 同步调用
- 5 / minute - 深度文章同步预览

### 6.4 全局限流器

```python
# app/main.py
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

---

## 7. 订阅号专属约束（区别于服务号）

| 限制 | 应对 |
|---|---|
| **无模板消息** (`errcode=43004`) | 项目已完全删除 `send_template_message` 路径，全走 `send_custom_message` |
| **每日群发配额** | `WECHAT_DAILY_PUBLISH_QUOTA` (默认 1)；`publish_article` 与 `publish_consumer` 入口都预检，超额置 DRAFT |
| **客服消息 48h 窗口** (`errcode=45015`) | `push_tasks` SQL 预过滤 `last_interact_time >= now-48h`；`push_consumer` 发之前单条再查；`webhook` 3 处刷新 `last_interact_time` |
| **无群发接口权限** (`errcode=48001`) | 个人订阅号专属，记录 `error_msg` 后退回 DRAFT，等运营手动群发 |
| **`draft/add` 必填 `thumb_media_id`** (`errcode=40007`) | 永远填；`cover_url` 走 `_upload_cover_from_url` 否则走默认图 (`WECHAT_DEFAULT_COVER_URL`) |
| **`draft/add` 字段硬限制** title 17 字符 / author 8 字节 / digest 超限 | 调用前显式截断；`digest` 由调用方传截断好的 summary |
| **客服群发接口** `/cgi-bin/message/mass/sendall` | 未申请此权限，代码侧未实现 |
| 网页授权只能用 `snsapi_userinfo` | 项目没用网页授权 |

### 7.1 字段上限实测表

| 字段 | 限制 | 实测 |
|---|---|---|
| `title` | 17 字符 (中文按字符计) | 17 OK / 18 errcode=45003 |
| `author` | 8 字节 UTF-8 | 9 字节 errcode=45110 |
| `digest` (description) | 约 54 字节实测安全值 | 超会 errcode=45004 |
| `content` | 无硬限，移动端 < 4000 字符体验佳 | |
| `thumb_media_id` | 必填 | |
| 封面图文件 | < 2 MB | > 2MB 拒收 |

---

## 8. 部署与运维

### 8.1 一次性配置

```bash
# 1. 克隆 + .env
cp .env.example .env
$EDITOR .env       # 必填: SECRET_KEY PG_PASSWORD REDIS_PASSWORD DEEPSEEK_API_KEY
                   # WECHAT_APP_ID WECHAT_APP_SECRET WECHAT_TOKEN

# 2. 一键启动 (含一次性 Alembic)
bash scripts/deploy.sh     # 等价: docker compose up -d
```

### 8.2 启动顺序

`docker-compose.yml` 用 `depends_on: { service_completed_successfully: ... }` 强制：

```
postgres / redis / kafka / zookeeper
     ↓
   migrate (Alembic upgrade head, 一次性)
     ↓
app / celery-worker / celery-beat / 3 consumers
     ↓
   (with-nginx profile)
     nginx
```

### 8.3 日常命令

```bash
# 看服务
docker compose ps

# 端到端冒烟
bash scripts/e2e_verify.sh

# 一键整流程
bash scripts/run_flow.sh

# 检查 digest 状态，提醒运营
bash scripts/check_publish.sh

# 看今天有什么
bash scripts/smoke_test.sh

# 看 consumer lag
docker exec wechat-kafka kafka-consumer-groups --bootstrap-server kafka:9092 --describe --group <group>
```

### 8.4 启动 / 重启

```bash
# 全启
docker compose up -d

# 只重启某个容器 (IP 会变, 公众号白名单需要重加)
docker compose up -d --force-recreate celery-worker

# 单独 update 一个 service (代码改动后)
docker compose up -d --build app celery-worker
```

### 8.5 数据迁移

```bash
# 改动 model 后生成迁移
alembic revision --autogenerate -m "add xxx"

# 应用
alembic upgrade head

# 容器化 (一次性)
# docker-compose.yml 的 `migrate` service:
#   command: alembic upgrade head
```

### 8.6 Celery beat 持久化

`celery-beat` 进程把调度进度写在 `/var/lib/celerybeat/celerybeat-schedule`。该目录被挂到 `celerybeat_data` 命名 volume，重启容器不丢调度。

### 8.7 关键 volume

| Volume | 挂载路径 | 内容 |
|---|---|---|
| postgres_data | `/var/lib/postgresql/data` | PG 数据 |
| redis_data | `/data` | RDB |
| kafka_data | `/var/lib/kafka/data` | Kafka 日志 |
| celerybeat_data | `/var/lib/celerybeat` | beat 调度进度 |

---

## 9. 已修复的 bug 历史

| # | 文件 | 修复 |
|---|---|---|
| - | `app/core/database.py` | `AsyncAdaptedQueuePool` → `QueuePool`（同步 engine 不能用异步池） |
| - | `app/models/topic.py` / `article.py` | ORM 属性改名 `extra_metadata`（PG 列名仍叫 `metadata`） |
| - | `alembic/versions/0002_*` | 加 `UNIQUE(topic_id, choice_id)` → Celery + Kafka 双路径并发不重复入库 |
| - | `alembic/versions/0003_*` | 加 `UNIQUE(openid, topic_id)` → 同一用户不能重复选同一话题 |
| #M1 | `app/api/v1/topics.py` | `POST /choice` 捕获 IntegrityError 转 400 |
| #M2 | `app/services/llm_clients/base.py` | `_parse_response` 不再用裸 `except` |
| #M4 | `app/tasks/push_tasks.py` | 从 `user_subscriptions` 读活跃用户，不硬编码 demo |
| #M5 | `app/tasks/push_tasks.py` | 仅当 PENDING 才推 PUSHED，不覆盖 SELECTED |
| #M7 | `app/services/wechat_core.py` | TTL < 60s 主动 refresh token |
| #M8 | `app/tasks/crawl_tasks.py` | 去重从「title 包含」改为「(title, heat_score) + 24h 窗口」 |
| #M9 | `app/api/v1/articles.py` | `POST /generate` 入口先 404，不让 Celery 内部失败 |
| #M12 | `consumers/*.py` | 持续失败转 DLQ + commit offset（防 Kafka 主题卡死）|
| #M14 | `app/services/kafka_producer.py` | BufferError 重试不再二次 `json.dumps` |
| #L2  | `app/api/v1/*.py` | `@limiter.limit("60/minute")` 加在所有列表端点 |
| #L6  | `app/services/llm_clients/base.py` | `generate_article(custom_prompt=)` 透传给 Celery |
| #L7  | `app/tasks/article_tasks.py` | `process_user_choices` limit: 10 → 200 |
| #L10 | `app/api/v1/articles.py` | `PUT /articles/{id}` 用 `exclude_unset=True` |
| #L12 | `app/services/wechat_core.py` | `create_draft` 强制 `thumb_media_id` |
| #12  | `app/core/rate_limit.py` | 全局 slowapi 限流器 |
| - | `app/services/wechat_core.py` | `_post_json()` 用 `ensure_ascii=False` 修 `\uXXXX` 中文显示 bug |
| - | `app/services/crawler.py` | 提取 `img` 字段 → 热搜配图 |
| - | `app/services/daily_digest.py` | 新格式 `1 # 标题\n 简讯\n 链接` |
| - | `app/services/llm_clients/base.py` | 加 `generate_brief(topic)` 30-40 字简讯 |

---

## 10. 关键设计汇总（不变量 / invariant）

| Idx | 设计 | 代码位置 |
|---|---|---|
| I1 | 同一 article_id 不会推到公众号两次 | `app/tasks/article_tasks.py:publish_article` + `consumers/publish_consumer.py` 共用 Redis lock + SELECT FOR UPDATE |
| I2 | 同一 (topic, choice) 文章只生成一次 | `app/tasks/article_tasks.py:generate_article` 查 unique + IntegrityError 兜底；consumer 同 |
| I3 | 同一 (user, topic) 只选一次 | `alembic/0003_*` + `app/api/v1/topics.py` 捕获 IntegrityError |
| I4 | Kafka 持续失败转 DLQ 不阻塞主流程 | `consumers/_dlq.py` |
| I5 | 用户不在 48h 窗口不发客服消息 | `consumers/push_consumer.py:_user_in_48h_window` |
| I6 | 配额耗尽立即退 DRAFT，不浪费微信调用 | `publish_article` / `daily_publish_digest` 双重预检 |
| I7 | 中文一律走 `ensure_ascii=False` 原生 UTF-8 | `wechat.py:_post_json()` |
| I8 | 草稿永远有 thumb_media_id（默认图兜底） | `wechat.py:create_draft` |
| I9 | `extra_metadata` 字段命名（PG 列名 `metadata`，ORM `extra_metadata`） | `app/models/*` |
| I10 | `last_interact_time` 在 3 处 webhook 都刷新 | `app/api/v1/webhook.py:_touch_interact` |

---

## 11. 环境变量关键项

```dotenv
# 必填
SECRET_KEY=<随机 hex 64>
PG_PASSWORD=wechat_user_pw
REDIS_PASSWORD=wechat_redis_pw
DEEPSEEK_API_KEY=sk-...
WECHAT_APP_ID=wx...
WECHAT_APP_SECRET=...
WECHAT_TOKEN=...                 # 微信回调校验

# 推荐填
WECHAT_DAILY_PUBLISH_QUOTA=1      # 个人订阅号 1；认证 4
WECHAT_DEFAULT_COVER_URL=https://picsum.photos/seed/cover/900/383
ADMIN_API_KEY=<随机 hex 64>      # 所有管理接口鉴权
PII_LOG_SALT=<随机 hex 16>       # 写日志 hash openid 用

# 调度
SCHEDULER_CRAWL_TIME=08:00
SCHEDULER_PUSH_TIME=09:00
SCHEDULER_TOPIC_LIMIT=10

# 功能开关
ENABLE_CRAWLER=true
ENABLE_WECHAT_PUSH=false          # 订阅号常态
ENABLE_AUTO_PUBLISH=true
ENABLE_KAFKA=true
ENABLE_CELERY=true
WECHAT_ENABLE_CUSTOM_MESSAGE=false  # 个人订阅号没有客服消息权限

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9093
KAFKA_TOPIC_CRAWL=crawl-topics
KAFKA_TOPIC_GENERATE=generate-articles
KAFKA_TOPIC_PUSH=push-notifications
KAFKA_TOPIC_PUBLISH=publish-articles
KAFKA_CONSUMER_GROUP=wechat-consumer-group
```

---

## 12. 已知待办

| 优先级 | 描述 |
|---|---|
| P1 | 给容器绑固定公网 IP (NAT) — 解决 IP 白名单每次重启失效 |
| P2 | 公众号"微信认证" — 解锁 48001 让 `publish_draft` 自动成功 |
| P3 | `publish_consumer` 不再处理 `cover_url`，改读 article.cover_url → 让 digest 路径和 publish 路径封面来源一致 |
| P4 | `scripts/check_publish.sh` 接 Slack / 邮件 webhook,自动推告警 |
| P5 | `app/api/v1/articles/preview` 同步等待 LLM,没超时 |
| P6 | 当前 `daily_publish_digest` 走 Celery 一条路径，没有 Kafka 版本 — 若 beat 挂则 digest 不发（应该容忍）|

---

## 13. 关键技术决策记录

### 13.1 为何用两套并行路径？

设计阶段的争论：Celery 直接调 vs 全 Kafka。最终选 **两套并存** 因为：
1. Celery 路径便于「管理员手动触发的同步任务」(`/publish` 接口直接调 `.delay()` 拿 task_id)
2. Kafka 路径解耦、重试、水平扩展更友好
3. 用同一把 Redis 锁解决并发冲突

### 13.2 为何不用 `asyncio` 一把梭？

历史包袱：原 WeChatService 是 `async def` + 内部 `requests` 阻塞 → Celery 跑得打嗝。改为全同步后，Celery / consumer / FastAPI 都能直接调用，少一堆 `asyncio.new_event_loop()` 胶水代码。

### 13.3 为何 `metadata`/`extra_metadata` 不同名？

SQLAlchemy `Base.metadata` 是保留属性，所以 ORM 属性叫 `extra_metadata`；列名是 `metadata`（PG JSONB）。读写例子：

```python
topic.extra_metadata = {"img_url": "..."}     # ORM 写法
row = db.execute(text("SELECT metadata FROM hot_topics WHERE id=:i"), {"i": 1}).first()
row[0]  # raw jsonb
```

---

## 14. 阅读顺序建议

1. **第一次接触**：先看 [0. 一句话] + [2. 总体架构] + [3. 数据模型]
2. **想改流程**：看 [5. 完整业务流程]，按你要改的步骤找 [4. 关键代码位置]
3. **想部署**：看 [8. 部署与运维]
4. **想加约束**：看 [10. 关键设计汇总] 的不变列表 + [7. 订阅号专属约束]
5. **遇到 bug**：看 [9. 已修复的 bug 历史] 排查同类

---

文档结束。
