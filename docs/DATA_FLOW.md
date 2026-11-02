# wechat-ai-publisher — 数据流转文档

> 文档基线:2026-07-24
> 范围:本项目三大存储(Redis / PostgreSQL / Kafka)的完整数据流转
> 阅读对象:负责改爬虫 / AI 生成 / 微信发布 / Celery / Kafka 任一路径的工程师,以及接手告警排查的运维同学

---

## 0. 与现有文档的关系

| 文档 | 主要内容 | 是否已覆盖本文档主题 |
|---|---|---|
| `docs/FLOW.md` | docker compose 当前运行状态、整体架构图、HotTopic / GeneratedArticle 状态机 | 部分(状态机、架构层) |
| `docs/TECHNICAL_FLOW.md` | 技术栈、总体架构、两条并行路径、容器拓扑 | 部分(架构层、Celery/Kafka 路径差异) |
| **`docs/DATA_FLOW.md`(本文档)** | Redis Key 清单、PG 表与 CRUD 行号、Kafka Topic payload 字段、端到端日流转、跨存储原子性、订阅号约束落地点 | **唯一详细的** |

本文档补的是另外两份只点到为止、不带行号 / 不带 payload 字段的部分。修改三大存储相关代码时,可以以本文档的"行号索引"快速定位。

---

## 1. Redis

### 1.1 客户端基础

- 实现:`app/core/redis_client.py`(基于 `redis.asyncio` + `ConnectionPool`)
- 连接池:`max_connections=20`、`decode_responses=True`
- 全局单例:`redis_client = RedisClient()`(`app/core/redis_client.py:198`)
- 同一个 Redis 实例上分三个逻辑 DB(不冲突):
  - `db=0`:业务缓存(access_token / 永久素材 / 分布式锁)
  - `db=1`:Celery broker
  - `db=2`:Celery result backend
- 同步调用入口:`redis_client.run_sync(...)`(`redis_client.py:134-141`)。Celery worker / Kafka consumer 没有运行中的 event loop 时,走内部持久化的 `_run_on_sync_loop` 而不是每次新建 loop,避免 redis async 客户端绑定已关闭 loop 的问题。

### 1.2 Key 命名清单(共 5 类业务 key)

| Key | TTL | 用途 | 设置 | 读取 | 删除 |
|---|---|---|---|---|---|
| `wechat:access_token:{appid}` | `expires_in - 300` 秒(≈ 6900s,微信 7200s 提前 5 分钟) | 微信 access_token 缓存(**按公众号隔离**) | `app/services/wechat_core.py` `get_access_token()` | 同上(TTL < 60s 主动 refresh) | 自然过期 / `force_refresh=True` 覆写 |
| `wechat:default_cover_media_id:{appid}` | 无(永久素材) | 默认封面永久素材 media_id 缓存(**按公众号隔离**) | `app/services/wechat_core.py` `_get_or_upload_default_cover()` | 同上 | 不删 |
| `wechat:cover_media_id:{appid}:{sha1(url)[:16]}` | 无(永久素材) | 自定义封面按 URL 哈希缓存 media_id(**按公众号隔离**) | `app/services/wechat_core.py` `_upload_cover_from_url()` | 同上 | 不删 |
| `wechat:lock:publish_article:{article_id}` | `settings.publish_lock_ttl_seconds`,默认 600s(10 分钟) | 发布文章分布式互斥锁 | `app/services/article_publishing.py:57-60`(Celery 与 Kafka 两条路径共用) | 不读值(只关心是否抢到) | `app/services/article_publishing.py` 内 `release_lock_sync` |
| `platform:lock:publish:{article_id}:{account_id}` | 120s(2 分钟) | 多平台发布互斥锁(按平台账号维度) | `app/services/article_publishing.py:270-271` | 不读值(只关心是否抢到) | `app/services/article_publishing.py` 内 `release_lock_sync` |

### 1.3 使用模式

#### access_token 懒加载 + 主动续期(`app/services/wechat_core.py` `get_access_token()`)

1. 调用任意微信 API 前先 `get_access_token()`
2. `GET wechat:access_token:{appid}` 同时查 TTL
3. TTL 剩余 < 60s(`_TOKEN_REFRESH_LEEWAY_SECONDS=60`)→ 主动 refresh
4. 微信返回 `errcode ∈ {40001, 42001, 40014, 40125}`(token 失效 / 过期 / 错签名 / 证书问题)→ 强制 refresh 一次再重试(`send_custom_message:134-139`、`publish_draft:460-465`)

#### 永久素材 media_id 缓存(`wechat.py:272-356`、`358-418`)

- 默认封面:`get_or_upload_default_cover` 上传一次,media_id 写 Redis
- 自定义封面:URL `sha1` 取前 16 位作为 key,避免重复上传
- > 2MB 文件上传会被拒绝(`wechat.py:309-313`)— 微信对图片素材的限制

#### 发布互斥锁(`publish_article`)

- `acquire_lock_sync(key, ttl_seconds=...)` 内部用 `SET NX EX` 抢锁。该方法自身默认 `ttl_seconds=60`,但调用方都显式传值:
  - 微信发布锁:`ttl_seconds=settings.publish_lock_ttl_seconds`(默认 **600 秒** = 10 分钟,`app/core/config.py:246`,调用点 `app/services/article_publishing.py:58-60`)
  - 多平台发布锁:`ttl_seconds=120`(2 分钟,`app/services/article_publishing.py:270-271`)
- `release_lock` 用 Lua 脚本仅当锁 value 匹配才释放,防止释放别人的锁
- **Celery 与 Kafka 两条路径共用同一把锁**:两条路径都委托给 `ArticlePublishingService.publish()`,锁 key 由 `_publish_lock_key()` 统一拼成 `wechat:lock:publish_article:{article_id}`(`app/services/article_publishing.py:31-33`)
- 多平台发布另有一把按账号维度的锁:`platform:lock:publish:{article_id}:{account_id}`

#### 限流

- `slowapi.Limiter` 默认无内存 / Redis backend,`storage_uri` 未配置时计数走进程内存
- 当前路由限流(`topics.py:26`、`articles.py:23/116/149`):
  - `GET /api/v1/topics` 60/min
  - `GET /api/v1/articles` 60/min
  - `POST /api/v1/articles/generate` 10/min
  - `POST /api/v1/articles/preview` 5/min

### 1.4 各执行路径下的 Redis 形态

| 路径 | 直接读写 Redis 的地方 |
|---|---|
| FastAPI 端点 | lifespan 调 `redis_client.get_client()`(`main.py:27`);限流中间件在路由装饰器上声明;**业务 KV 不在路由里读写** |
| Celery 任务 | `publish_article` 用 `acquire_lock_sync` / `release_lock_sync`;其余用 `redis_client.run_sync(...)` 走同步 loop |
| Kafka consumer | `publish_consumer` 调 `ArticlePublishingService.publish()`,锁由 `app/services/article_publishing.py` 内的 `acquire_lock_sync` 取得;`generate_consumer` / `push_consumer` 不直接读写 Redis |
| 微信 Webhook | 不直接读写 Redis;`WeChatService.get_access_token` 在所有微信 API 调用前懒加载 |

---

## 2. PostgreSQL

### 2.1 连接与会话

- 引擎:`app/core/database.py:30-45`
  - `poolclass=QueuePool`(同步,不是 `AsyncAdaptedQueuePool` — 那是 `create_async_engine` 才用的)
  - `pool_size=20`、`max_overflow=40`、`pool_pre_ping=True`、`pool_recycle=3600`
  - `connect_args` 含 TCP keepalives 避免被中间网络设备静默断连
- Session 工厂:`SessionLocal(autocommit=False, autoflush=False)`(`database.py:47`)
- 两种使用模式:
  - FastAPI:**`Depends(get_db)`** 迭代器(`database.py:51-57`),路由显式 `db.commit()`,依赖结束时 `db.close()`
  - Celery / 一次性脚本 / Kafka consumer:**`session_scope()`** 上下文管理器(`database.py:60-71`),自动 commit / rollback / close
- 迁移:Alembic 脚本(`alembic/versions/`),起点是 `0001_initial_schema.py`、`0002_add_fk_and_unique_constraint.py`、`0003_user_choice_unique_constraint.py`,后续还有 `0004_admin_config_tables`、`add_platform_accounts`、`seed_all_platforms`、`create_llm_providers`、`create_platform_ip_whitelist` 等。容器启动时通过 `migrate` 一次性服务跑 `alembic upgrade head`,业务容器 `depends_on: service_completed_successfully`。
- `init_db()` 已废弃(`app/core/database.py:75-82`,空函数 + 警告),**不建表**;建表只走 Alembic。

### 2.2 表清单

#### `hot_topics`(`app/models/topic.py:19-39`)

| 字段 | 类型 | 约束 / 默认 |
|---|---|---|
| `id` | Integer | PK, autoincrement |
| `topic_id` | UUID | unique, not null |
| `title` | String(200) | not null |
| `heat_score` | Float | not null |
| `source_url` | String(500) | nullable(原始百度聚合 URL) |
| `summary` | Text | nullable |
| `category` | String(50) | nullable |
| `keywords` | JSONB | default `list` |
| `status` | Enum(TopicStatus) | default `PENDING` |
| `extra_metadata`(DB 列名 `metadata`) | JSONB | default `dict`(存 `news_url`、`img_url`) |
| `create_time` / `update_time` | DateTime | `server_default=now()`,后者 `onupdate=now()` |
| `push_time` | DateTime | nullable |

索引:`ix_hot_topics_status_create (status, create_time)`、`ix_hot_topics_heat (heat_score)`

#### `user_choices`(`topic.py:41-64`)

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | Integer | PK |
| `choice_id` | UUID | unique |
| `user_openid` | String(100) | not null |
| `topic_id` | Integer | FK→hot_topics.id `ON DELETE CASCADE` |
| `choice_index` | Integer | nullable |
| `choose_time` | DateTime | default `now()` |
| `is_processed` | Boolean | default `false`(消费 marker) |
| `process_time` | DateTime | nullable |

索引:`(user_openid, is_processed)`、`(topic_id)`
唯一约束:`uq_user_choice_openid_topic (user_openid, topic_id)`(alembic 0003 加入)

#### `generated_articles`(`app/models/article.py:20-57`)

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | Integer | PK |
| `article_id` | UUID | unique |
| `topic_id` | Integer | FK→hot_topics.id `ON DELETE CASCADE` |
| `choice_id` | UUID | nullable(**不**做 DB FK,靠唯一约束) |
| `user_openid` | String(100) | nullable(客服消息时回填) |
| `title` / `content` | String(200) / Text | not null |
| `summary` / `cover_url` | Text / String(500) | nullable |
| `tags` | JSONB | default `list` |
| `status` | Enum(ArticleStatus) | default `GENERATING` |
| `wechat_media_id` / `wechat_publish_id` | String(100) | nullable |
| `error_msg` | Text | nullable |
| `extra_metadata` | JSONB | default `dict`(存 `digest_date`、`is_digest`、`topic_count` 等) |
| `create_time` / `update_time` | DateTime | 同上 |
| `publish_time` | DateTime | nullable |

索引:`(status, create_time)`、`(wechat_media_id)`、`(choice_id)`
唯一约束:`uq_articles_topic_choice (topic_id, choice_id)` — NULL choice_id 不参与约束,digest 路径可绕过。

#### `user_subscriptions`(`app/models/user.py:9-32`)

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | Integer | PK |
| `user_id` | UUID | unique |
| `openid` | String(100) | unique, indexed |
| `unionid` | String(100) | indexed |
| `nickname` | String(100) | nullable |
| `is_active` | Boolean | default `true` |
| `subscribe_time` | DateTime | `server_default=now()` |
| `last_interact_time` | DateTime | `server_default=now(), onupdate=now()`(**48h 窗口预检的依赖字段**) |

索引:`(is_active, last_interact_time)`

#### 其余 7 张表(`app/models/*.py`)

| 表名 | 说明 |
|---|---|
| `admin_users` | 管理后台用户(username 唯一 / password_hash / role / is_active) |
| `ip_whitelist` | 访问 IP 白名单规则 |
| `platform_ip_whitelist` | 各发布平台的 IP 白名单配置 |
| `llm_providers` | LLM 供应商(provider_type / api_url / 加密 api_key / model / is_default) |
| `platforms` | 平台定义(platform_type 唯一,对应 `PlatformType` 枚举) |
| `platform_accounts` | 平台账号(加密凭证 / crawl_cron / push_cron / 软删除) |
| `system_settings` | 系统级 KV 配置 |

### 2.3 状态机

**HotTopic**(`TopicStatus`,`topic.py:12-17`):
```
PENDING ──push_topics_to_users──▶ PUSHED ──用户点击菜单/POST /choice──▶ SELECTED ──article 生成完成──▶ PROCESSED
   └────────── 任何路径失败 ──────────────────────────────────────────────────────────────▶ FAILED
```

注:`PROCESSED` 当前主要靠 `user_choices.is_processed=true` 表达,`hot_topics.status` 实际流转不强制写 `PROCESSED`。

- PENDING → PUSHED:`app/tasks/push_tasks.py:123-125`(判断当前 == PENDING 才推,避免覆盖 SELECTED)
- PENDING/PUSHED → SELECTED:`api/v1/topics.py:193`、`api/v1/webhook.py:293-294`

**GeneratedArticle**(`ArticleStatus`,`article.py:12-18`):
```
GENERATING ──LLM 返回──▶ GENERATED ──publish_article / publish_consumer──▶ PUBLISHING
                                       └─ 配额耗尽 / 草稿失败 ─▶ DRAFT (+ error_msg)
PUBLISHING ──微信 errcode=0──▶ PUBLISHED
```

### 2.4 CRUD 关键读写点(行号索引)

| 操作 | 表 | 位置 |
|---|---|---|
| INSERT HotTopic | hot_topics | `app/tasks/crawl_tasks.py:50-61`(同 title+heat_score 且 24h 内视为已存在) |
| INSERT UserChoice | user_choices | `app/api/v1/topics.py:180-185` / `app/api/v1/webhook.py:276-281` |
| INSERT GeneratedArticle | generated_articles | `article_tasks.py:93-103` / `consumers/generate_consumer.py:182-192` / `article_tasks.py:465-484` 与 `494-510`(digest) |
| INSERT/UPDATE UserSubscription | user_subscriptions | `app/api/v1/webhook.py:218-227` |
| UPDATE HotTopic.status | hot_topics | `push_tasks.py:123-125` / `topics.py:193` / `webhook.py:293-294` |
| UPDATE GeneratedArticle 状态 | generated_articles | `article_tasks.py:202-275` / `publish_consumer.py:178-248` / `article_tasks.py:521-577`(digest) |
| `SELECT FOR UPDATE` | generated_articles | `article_tasks.py:192-197` / `publish_consumer.py:167-172` |
| UPDATE UserChoice.is_processed | user_choices | `article_tasks.py:138-140` / `generate_consumer.py:226-228` |
| UPDATE UserSubscription.last_interact_time | user_subscriptions | `webhook.py:53-55`(`_touch_interact`) |
| SELECT PENDING 话题 | hot_topics | `topics.py:36-46/56-66/73-83` / `push_tasks.py:59-67` / `webhook.py:75-88` |
| SELECT 48h 内活跃 openid | user_subscriptions | `push_tasks.py:78-87` |
| SELECT last_interact_time 单条 | user_subscriptions | `push_consumer.py:218-222` |
| SELECT 已生成查询(幂等) | generated_articles | `article_tasks.py:68-75` / `generate_consumer.py:155-162` |
| SELECT 今日 PUBLISHED 数 | generated_articles | `article_tasks.py:222-229/449-456` / `publish_consumer.py:193-200` |
| digest 幂等(JSONB 路径) | generated_articles | `article_tasks.py:395-401`(`extra_metadata["digest_date"].astext == today_str`) |

### 2.5 事务边界与并发安全

- **FastAPI 路由**:`Depends(get_db)` 不自动 commit,路由 handler 显式 `db.commit()` 再让依赖关闭连接。位置:`topics.py:93/116/137/194` 等。
- **Celery 任务**:统一 `with session_scope() as db:`,自动 commit / rollback。位置:`crawl_tasks.py:29`、`push_tasks.py:56`、`article_tasks.py:61/190/393`。
- **Kafka consumer**:同样 `with session_scope() as db:`。位置:`generate_consumer.py:147`、`publish_consumer.py:166`。
- **唯一约束竞争**:两条路径都捕获 `IntegrityError` → `db.rollback()` → 复用已有行(`article_tasks.py:104-130`、`generate_consumer.py:193-220`)。这是 Celery + Kafka 并发去重的核心。

---

## 3. Kafka

### 3.1 配置

- broker:`settings.kafka_bootstrap_servers`(默认 `kafka:9093`,`config.py:55`)
- 消费者组派生(`settings.kafka_consumer_group=wechat-consumer-group`,`config.py:56`):
  - `wechat-consumer-group-generate`(`consumers/generate_consumer.py:28`)
  - `wechat-consumer-group-publish`(`consumers/publish_consumer.py:38`)
  - `wechat-consumer-group-push`(`consumers/push_consumer.py:33`)
- Producer(`app/services/kafka_producer.py:42-51`):
  - `acks=all` + `enable.idempotence=true` + `compression.type=snappy`
  - `linger.ms=20`、`message.send.max.retries=3`
- Topic 预创建:`app/core/kafka_client.py:14-19`,容器启动通过 `confluent_kafka.admin.AdminClient` 创建,分区 3、副本 1,已存在跳过。

### 3.2 Topic 清单

| Topic 默认名 | 配置项 | Producer | Consumer |
|---|---|---|---|
| `crawl-topics` | `kafka_topic_crawl` | `send_crawl_topics`(预留,**当前无调用方**) | — |
| `generate-articles` | `kafka_topic_generate` | `app/tasks/article_tasks.py:338`(`process_user_choices`) | `consumers/generate_consumer.py` |
| `push-notifications` | `kafka_topic_push` | `article_tasks.py:303` / `push_tasks.py:116` / `publish_consumer.py:270` / `generate_consumer.py:168/212/234` / `push_consumer.py:151/169/196` | `consumers/push_consumer.py` |
| `publish-articles` | `kafka_topic_publish` | `consumers/generate_consumer.py:242`(`enable_auto_publish=True` 时) | `consumers/publish_consumer.py` |
| `wechat-dlq` | `kafka_topic_dlq` | `consumers/_dlq.py:79` | —(无消费者) |

> **手动发布不经过 Kafka**:`POST /api/v1/articles/{id}/publish` 直接 `publish_article.delay()`(`articles.py:195`)走 Celery。

### 3.3 消息 payload 字段

`KafkaProducerService._send`(`kafka_producer.py:77-120`)统一 `json.dumps(...).encode('utf-8')`,**所有 message key 当前都是 None**,分区由 round-robin 决定。

| 方法 | payload 字段 | 调用方 |
|---|---|---|
| `send_generate_request` | `{topic_id, choice_id, user_openid}` | `app/tasks/article_tasks.py:338-342` |
| `send_article_generated`(article-ready) | `{article_id, topic_id, title, user_openid}`(无 `publish_status`) | `consumers/generate_consumer.py:168/212/234` |
| `send_article_generated`(publish-success) | 同上 + `publish_status: "published"` | `article_tasks.py:303-309`、`publish_consumer.py:270-276` |
| `send_publish_request` | `{article_id, publish_type: "auto"}` | `consumers/generate_consumer.py:242-245` |
| `send_system_event` | `{type, user_openid, article_id?, topic_id?, title?}`,`type ∈ {"topic_pushed", "push_success", "push_skipped_disabled", "push_skipped_48h_window"}` | `push_tasks.py:116`、`push_consumer.py:151/169/196` |

### 3.4 Consumer 通用框架

- `enable.auto.commit=false` + `enable.auto.offset.store=false`(必须手动 `commit`)
- `auto.offset.reset=earliest`
- 死信处理:`DeadLetterHandler`(`consumers/_dlq.py`)按 `(topic, partition, offset)` 进程内计数,超过 `kafka_consumer_max_attempts`(默认 5)→ 发到 `kafka_topic_dlq`(envelope:`{original_topic, partition, offset, reason, ts, payload}`)+ 同步 `commit` 当前 offset
- **JSON 解析失败 → 立即 DLQ**
- **业务校验失败(缺字段/topic 不存在)→ 不抛异常,正常返回并 commit**(避免重复消费死循环)

### 3.5 Celery 与 Kafka 的并行/互斥关系

```
                   ┌──────────────────┐
用户选择 ──────────▶│  POST /choice    │
                   │  或菜单 CLICK    │
                   └────────┬─────────┘
                            ▼
                  INSERT UserChoice (Celery 路径只这一步)
                            │
                            ▼
              每 30 分钟 beat → process_user_choices
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   .delay() Celery 生成            send_generate_request → Kafka
       (article_tasks)            (generate_consumer)
              │                           │
              └──────── 共享唯一约束 ───────┘
              (IntegrityError 兜底 + 复用查询)
```

| 阶段 | Celery 路径 | Kafka 路径 | 互斥机制 |
|---|---|---|---|
| 触发生成 | `POST /api/v1/articles/generate` → `generate_article.delay()`(`articles.py:135-140`) | `POST /choice` / CLICK → `UserChoice` → `process_user_choices` 发 `send_generate_request` | `uq_articles_topic_choice` + `IntegrityError` 兜底 |
| 触发发布 | `POST /api/v1/articles/{id}/publish` → `publish_article.delay()`(`articles.py:195`)**永远走 Celery** | 仅 `enable_auto_publish=True` 时由 `generate_consumer` 发 `send_publish_request` | Redis 锁 `wechat:lock:publish_article:{id}` + DB `SELECT FOR UPDATE` + status 早退 |
| 推送通知 | **Celery 不直接发送推送**,统一改由 `article_tasks.py:303` 发 `send_article_generated` 给 `kafka_topic_push` 让 `push_consumer` 处理 | `push_consumer` 监听 `kafka_topic_push`,48h 窗口预检后调 `send_custom_message` | Kafka 路径唯一 |

### 3.6 重试与 DLQ

| 路径 | 策略 |
|---|---|
| Kafka consumer | `DeadLetterHandler` 进程内计数,`kafka_consumer_max_attempts`(默认 5)次失败 → DLQ |
| Celery task | `max_retries=3`,退避 `countdown=300/120/600s`(`crawl_tasks.py:77`、`push_tasks.py:140`、`article_tasks.py:155/290`),耗尽后 FAILURE |
| 数据库约束 | `IntegrityError` 兜底查询 |

---

## 4. 端到端日流转

```
08:00  beat → crawl_hot_topics                       (Celery 路径)
       └─ BaiduHotCrawler.get_hot_topics() → 同 title+heat_score 24h 去重 → INSERT hot_topics(status=PENDING)

09:00  beat → push_topics_to_users
       └─ 读 Redis access_token → SELECT PENDING 话题 + 48h 内活跃 openid
          UPDATE PENDING→PUSHED → send_system_event(type=topic_pushed) → push topic

09:00 ~ 23:59  用户在公众号内点击菜单 / POST /choice      (FastAPI 同步路径)
       └─ INSERT user_choices(unique) → UPDATE hot_topics.status=SELECTED
          CLICK 事件 → webhook.py:_touch_interact 刷新 last_interact_time

每 30 分钟  beat → process_user_choices
       └─ SELECT is_processed=false 的 UserChoice(limit 200) → send_generate_request
          → generate_consumer → LLM → INSERT generated_articles(GENERATED)
          → UPDATE user_choices.is_processed=true → send_article_generated(article-ready)

持续  push_consumer 处理 push topic
       └─ 48h 窗口预检 → send_custom_message → send_system_event 统计

持续(enable_auto_publish=True)  publish_consumer 处理 publish topic
       └─ Redis 锁 wechat:lock:publish_article:{id} → SELECT FOR UPDATE GeneratedArticle
          → 配额预检 → create_draft + publish_draft → UPDATE status=PUBLISHED/DRAFT
          → send_article_generated(publish_status=published) → push topic

手动  POST /publish 走 Celery publish_article(同 publish_consumer 业务逻辑)

09:30  beat → daily_publish_digest
       └─ digest 幂等(JSONB extra_metadata.digest_date) → SELECT 今日 top 话题
          → LLM 简讯 → INSERT/UPDATE generated_articles → create_draft + publish_draft
          (不发推送通知,草稿本身已触达)

失败  Kafka 5 次 → DLQ / Celery 3 次 → FAILURE / DB IntegrityError → 复用查询
```

---

## 5. 关键边界 / 陷阱

### 5.1 跨存储原子性

- **"先库后消息"**:`_send_*_notification` 都在 `db.commit()` 之后才发(`article_tasks.py:278-281`、`publish_consumer.py:248-252`)。Kafka 发送失败只记日志,不影响数据库结果。
  - 风险:commit 后进程崩溃 → Kafka 通知丢失 → 用户收不到推送通知。**当前业务可接受**(用户从菜单重选即可重新触发)。
- **Redis 锁与 DB 事务**:锁在事务外获取,事务内 `with_for_update()` 二次校验。微信发布锁 TTL 取 `settings.publish_lock_ttl_seconds`(默认 600s)远大于单次发布事务时长,避免锁过期窗口;多平台发布锁 TTL 120s。
- **唯一约束竞态兜底**:`uq_articles_topic_choice` + `IntegrityError` rollback + 复用查询,保证同一 `(topic_id, choice_id)` 在 Celery / Kafka 双路径并发下也只入库一次。

### 5.2 发布/生成已抽到共享 Service 层

- **发布**:Celery 的 `publish_article`(`app/tasks/article_tasks.py:59`)与 Kafka 的 `publish_consumer.process_message()`(`consumers/publish_consumer.py:121`)现在**都委托给** `ArticlePublishingService.publish()`(`app/services/article_publishing.py`),差异只剩重试机制(Celery `self.retry()` vs Kafka `DeadLetterHandler`)。
- **生成**:同样抽取为 `ArticleGenerationService.generate()`(`app/services/article_generation.py`),由 `app/tasks/article_tasks.py` 的 `generate_article` 与 `consumers/generate_consumer.py` 共用。
- 行为一致性靠共享 Service + Redis 锁 + 唯一约束保证。

### 5.3 订阅号约束在三大存储的落地点

| 约束 | 检查位置 | 落点 |
|---|---|---|
| 每日发布配额(个人 1 / 认证订阅号 4) | `wechat.py:39` / `article_tasks.py:222-249` / `article_tasks.py:449-491` / `publish_consumer.py:193-213` | `GeneratedArticle.status=DRAFT + error_msg` |
| 客服消息 48h 窗口(`errcode=45015/48001`) | `push_tasks.py:77-100` / `webhook.py:53-55` / `push_consumer.py:210-230` | `user_subscriptions.last_interact_time` |
| access_token 7200s 过期 | `app/services/wechat_core.py` `get_access_token()` | Redis `wechat:access_token:{appid}` |
| 永久素材 5000 张配额 | `wechat.py:309-313` | Redis `wechat:cover_media_id:*` |
| 单次推送上限 1000 用户 | `push_tasks.py:84` | SQL `.limit(1000)` |
| 选择去重(user+topic) | `topics.py:186-190` / `webhook.py:282-290` / alembic 0003 | PG 唯一约束 `uq_user_choice_openid_topic` |
| 文章去重(topic+choice) | `article_tasks.py:104-130` / `generate_consumer.py:193-220` | PG 唯一约束 `uq_articles_topic_choice` |
| 发布去重(同 article 不重复发布) | `article_tasks.py:180-187` / `publish_consumer.py:156-163` | Redis 锁 + PG status 检查 |
| digest 每日幂等 | `article_tasks.py:395-411` | PG JSONB `extra_metadata.digest_date` |

### 5.4 其他值得注意的点

- **`enable_auto_publish`**(`config.py:143`,默认 `false`):手动发布走 Celery;auto 模式才走 `kafka_topic_publish`。
- **`wechat_enable_custom_message`**(`config.py:102`,默认 `true`):未认证订阅号置 `false` 直接跳过客服消息(`push_tasks` 和 `push_consumer` 都生效),避免 48001 刷错误日志。
- **`wechat_daily_publish_quota`** 默认 1(个人订阅号),生产环境必须改为对应账号类型。
- **PII 脱敏**(`app/core/pii.py`):openid 在所有 INFO 日志里走 HMAC-SHA256 + `pii_log_salt`(salt 在 `config.py:27`)。
- **Kafka consumer 重启后 DLQ 计数清零**:`DeadLetterHandler` 计数在进程内存,重启后重新计数但仍能升级 DLQ(`_dlq.py:9-12`)。
- **`session_scope` 与 `get_db` 的 commit 边界**:`session_scope` 自动 commit(`database.py:66`),`get_db` 只 close 不 commit(路由必须显式调用)。
- **`crawl-topics` topic 当前无 producer 调用方**:`send_crawl_topics` 在 `kafka_producer.py:58-59` 存在但未被引用,保留供将来"把爬虫也接入 Kafka"重构。

---

## 6. 核心文件索引

| 类别 | 路径 |
|---|---|
| 配置 | `app/core/config.py` |
| 数据库 | `app/core/database.py` |
| Redis | `app/core/redis_client.py` |
| Kafka 客户端 | `app/core/kafka_client.py`、`app/services/kafka_producer.py` |
| 模型 | `app/models/topic.py`、`app/models/article.py`、`app/models/user.py`、`app/models/platform.py`、`app/models/llm_provider.py`、`app/models/admin_user.py`、`app/models/ip_whitelist.py`、`app/models/platform_ip_whitelist.py`、`app/models/system_setting.py` |
| 业务服务 | `app/services/crawler.py`、`app/services/llm_clients/`、`app/services/llm_provider.py`、`app/services/wechat_core.py`、`app/services/wechat.py`、`app/services/wechat_publish.py`、`app/services/daily_digest.py`、`app/services/article_generation.py`、`app/services/article_publishing.py` |
| Celery | `app/tasks/celery_app.py`、`app/tasks/crawl_tasks.py`、`app/tasks/push_tasks.py`、`app/tasks/article_tasks.py` |
| FastAPI | `app/main.py`、`app/api/v1/topics.py`、`app/api/v1/articles.py`、`app/api/v1/webhook.py` |
| Kafka consumer | `consumers/generate_consumer.py`、`consumers/publish_consumer.py`、`consumers/push_consumer.py`、`consumers/_dlq.py` |
| 辅助 | `app/core/auth.py`、`app/core/rate_limit.py`、`app/core/pii.py`、`app/api/deps.py` |
| 迁移 | `alembic/versions/`（`0001_initial_schema` → `0003_user_choice_unique_constraint` → `0004_admin_config_tables` → `add_platform_accounts` → `seed_all_platforms` → `create_llm_providers` → `create_platform_ip_whitelist` 等） |
