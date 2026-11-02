# 项目总览

## 一、需求与目的

**核心需求**：自动化微信公众号内容生产与发布流程，减少人工运营成本。

用户故事：定时爬取百度热搜 → 推送给用户 → 用户选择感兴趣的话题 → AI 生成文章 → 自动发布到微信公众号。

---

## 二、整体实现方式

```
百度热搜  ──爬虫──▶  话题库  ──推送──▶  用户选择  ──AI生成──▶  文章库  ──发布──▶  微信公众号
   │                                                              │
   └────────────────────────── 定时任务调度 ───────────────────────┘
```

---

## 三、功能模块结构

| 模块 | 所在文件 | 职责 |
|---|---|---|
| **爬虫** | `app/services/crawler.py` | 爬取百度热搜，按 title+date 去重，写入 hot_topics |
| **用户交互** | `app/api/v1/webhook.py` | 微信回调：关注/点击/消息 → 刷新 48h 互动时间、接收用户话题选择 |
| **AI 生成** | `app/services/llm_clients/` | 调用 LLM API（DeepSeek / OpenAI / MiniMax / Qwen 可配置），构造 system+user prompt，宽松 JSON 解析 |
| **微信发布** | `app/services/wechat_core.py` + `app/services/wechat.py` | 发送客服消息、创建草稿、提交发布（订阅号专用，无模板消息）；`wechat_core.py` 是唯一实现，`wechat.py` 只是 `.env` 凭据适配器 |
| **定时调度** | `app/tasks/celery_app.py` | Celery Beat：08:00 爬虫 / 09:00 推送（已弃用）/ 09:30 每日 digest / 每 30 分钟处理用户选择 / 按账号动态注入 `crawl-{account_id}` / `push-{account_id}` |
| **爬虫任务** | `app/tasks/crawl_tasks.py` | `crawl_hot_topics`：调用 BaiduHotCrawler，按 title+date 去重写入 hot_topics |
| **文章任务** | `app/tasks/article_tasks.py` | `generate_article` / `publish_article` / `process_user_choices` / `daily_publish_digest` / `generate_deep_article` |
| **推送任务** | `app/tasks/push_tasks.py` | `push_topics_to_users` / `push_for_account`：发送客服消息给活跃用户（`_send_template()` 只是历史命名，实际调 `send_custom_message` 客服消息） |
| **生成消费者** | `consumers/generate_consumer.py` | 订阅 Kafka `generate-articles` 主题，调用 `ArticleGenerationService`（内部走 LLM 客户端） |
| **推送消费者** | `consumers/push_consumer.py` | 订阅 Kafka `push-notifications` 主题，调用 send_custom_message |

---

## 四、技术栈

| 层级 | 技术 |
|---|---|
| Web 框架 | FastAPI + Pydantic v2 |
| 数据库 | PostgreSQL + SQLAlchemy 2 + Alembic |
| 缓存 | Redis |
| 任务队列 | Celery + Celery Beat |
| 消息队列 | Kafka（confluent-kafka） |
| AI | 多 LLM 供应商（DeepSeek / OpenAI / MiniMax / Qwen），配置存 `llm_providers` 表 |
| 微信 | 微信公众平台 API（订阅号模式） |
| 部署 | Docker Compose |

---

## 五、数据流与双路径

项目有 **两套并行执行路径**，各自都能完成「生成文章」「通知用户」的功能：

```
路径一：Celery 任务
  Celery Beat 定时触发
    → crawl_hot_topics        (爬虫任务，08:00)
    → crawl_for_account       (按账号爬虫，账号自定义 crawl_cron)
    → push_topics_to_users     (推送任务，09:00，已弃用)
    → push_for_account        (按账号推送，账号自定义 push_cron)
    → daily_publish_digest     (每日热搜早报，09:30，默认主调度)
    → process_user_choices     (处理用户选择，每 30 分钟)
    → generate_article         (生成文章)
    → publish_article          (发布文章)

路径二：Kafka 消费者
  Kafka 消息触发
    → generate_consumer       (订阅 kafka_topic_generate → 调用 ArticleGenerationService → 发送 push-notifications / publish-articles)
    → push_consumer           (订阅 kafka_topic_push → 调用 send_custom_message)
```

其中 `crawl-{account_id}` / `push-{account_id}` 由 `_add_per_account_schedules()`（`app/tasks/celery_app.py:110-152`）按 `PlatformAccount.crawl_cron` / `push_cron` 动态注入 beat 调度。

FastAPI HTTP 接口默认走 Celery 路径（`.delay()`）。

### Kafka 主题命名

| 主题 | 用途 |
|---|---|
| `crawl-topics` | 爬虫结果 |
| `generate-articles` | 文章生成 |
| `push-notifications` | 推送通知 |
| `publish-articles` | 发布文章 |
| `wechat-dlq` | 死信队列（Kafka consumer 连续失败超阈值后进这里） |

### Celery 队列

| 队列 | 用途 |
|---|---|
| `crawl` | 爬虫任务（`crawl_hot_topics` / `crawl_for_account`） |
| `article` | 文章生成/发布任务（`generate_article` / `publish_article` / `daily_publish_digest` / `process_user_choices` / `generate_deep_article`） |
| `push` | 推送任务（`push_topics_to_users` / `push_for_account`） |
| `default` | 未使用（保留） |

---

## 六、订阅号特殊约束

本项目运行在**订阅号**上（非服务号），有以下硬性限制：

| 约束 | 说明 | 处理方式 |
|---|---|---|
| **无模板消息** | 订阅号无 `template/send` 接口权限（`app/services/wechat.py`（`send_custom_message`）） | 全部改用 `send_custom_message` 客服消息 |
| **每日群发配额** | 个人订阅号 1 篇/天，认证订阅号/服务号 4 篇/天 | 通过 `WECHAT_DAILY_PUBLISH_QUOTA` 配置，配额耗尽时降级为"只存草稿" |
| **客服消息 48h 窗口** | `send_custom_message` 只能发给 48h 内互动过的用户 | SQL 预过滤 `last_interact_time >= now-48h`；webhook 在 3 处刷新互动时间 |

> **主调度说明**：`09:00` 的 `push_topics_to_users` 已弃用（订阅号无客服消息权限，推送触达率近 0），代码保留供 `enable_wechat_push=true` 的认证号复用；**默认主调度是 `09:30` 的 `daily_publish_digest`**（群发一篇日报，覆盖所有粉丝）。

---

## 七、数据库模型

共 11 张表（`app/models/*.py` 的 `__tablename__`）：

| 模型 | 表名 | 说明 |
|---|---|---|
| `HotTopic` | `hot_topics` | 百度热搜话题，状态：PENDING / PUSHED / SELECTED / PROCESSED / FAILED |
| `UserChoice` | `user_choices` | 用户对话题的选择记录 |
| `GeneratedArticle` | `generated_articles` | AI 生成的文章，状态：GENERATING / GENERATED / PUBLISHING / PUBLISHED / DRAFT / FAILED |
| `UserSubscription` | `user_subscriptions` | 公众号订阅用户，`last_interact_time` 是客服消息 48h 窗口的判定字段 |
| `AdminUser` | `admin_users` | 管理后台用户（JWT 登录） |
| `IPWhitelist` | `ip_whitelist` | 访问 IP 白名单规则 |
| `PlatformIPWhitelist` | `platform_ip_whitelist` | 各发布平台的 IP 白名单配置 |
| `LLMProvider` | `llm_providers` | LLM 供应商配置（类型 / API URL / 加密 API Key / 模型名） |
| `Platform` | `platforms` | 平台定义（`PlatformType` 枚举） |
| `PlatformAccount` | `platform_accounts` | 平台账号（加密凭证 / per-account cron 调度 / 软删除） |
| `SystemSetting` | `system_settings` | 系统级 KV 配置 |

字段命名注意：ORM 属性统一叫 `extra_metadata`（避免与 SQLAlchemy `Base.metadata` 保留属性冲突），数据库列名仍为 `metadata`。

### 7.1 多平台发布

- 平台枚举 `PlatformType`（`app/models/platform.py:14-20`）共 6 个：`WECHAT` / `DOUYIN` / `XIAOHONGSHU` / `BAIJIAHAO` / `TOUTIAO` / `YOUTUBE`。
- `PlatformServiceFactory`（`app/services/platforms/__init__.py:18-24`）注册了全部 6 个平台实现（`app/services/platforms/*_service.py`）。
- `PlatformAccount.credentials_encrypted` 用 Fernet 加密保存平台凭证，`credentials_masked` 仅用于前端展示；`crawl_cron` / `push_cron` 支持每个账号自定义调度。
- 发布入口：`POST /api/v1/articles/{id}/publish-to-platform` → Kafka 投递 → `ArticlePublishingService.publish_to_platform()`（`app/services/article_publishing.py`）→ 目标平台 Service → 回写 `platform_media_id`。
- 多平台发布使用独立锁 `platform:lock:publish:{article_id}:{account_id}`（TTL 120 秒）。

### 7.2 多 LLM 供应商

- 供应商类型：`deepseek` / `openai` / `minimax` / `qwen`，客户端工厂在 `app/services/llm_clients/__init__.py`（`get_llm_client()` / `build_llm_client()`）。
- 配置存 `llm_providers` 表：API URL、Fernet 加密的 API Key、模型名、temperature、timeout、`is_default`（同一时间只有一个默认）。
- 模型名不再写死：优先取 `llm_providers.model`，未配置时回落到 `DEEPSEEK_MODEL`（默认 `deepseek-chat`，`app/core/config.py:156`）。
- 管理接口：`/api/v1/llm-providers`（增删改查、设默认），管理后台「LLM 模型配置」页面。

### 7.3 管理后台 / JWT 认证

- `admin_users` 表 + `app/core/jwt_auth.py`（签发 / 校验 JWT），登录接口 `POST /api/v1/auth/login`，另有 `refresh` / `me` / `change-password` 与用户管理。
- 鉴权策略 `require_admin_auth`（`app/core/auth.py`）：**JWT Bearer 优先**，`X-Admin-Token` 作为 fallback（`ADMIN_API_KEY`）。
- 管理接口覆盖：平台与平台账号、平台 IP 白名单、访问 IP 白名单、LLM 供应商、系统设置、关键词文章、Celery 任务查询与 beat 重载。

---

## 八、部署架构

```
┌──────────────────────────────────────────────────────────────┐
│                      Docker Compose                          │
│  wechat-postgres / wechat-redis / wechat-kafka / wechat-kafka-ui │
│  wechat-app (FastAPI) / wechat-celery-worker / wechat-celery-beat │
│  wechat-consumer-generate / wechat-consumer-push             │
│  (optional) wechat-nginx                                    │
└──────────────────────────────────────────────────────────────┘
```

服务访问入口：
- API: http://localhost:8888
- API 文档 (Swagger): http://localhost:8888/docs
- Kafka UI: http://localhost:9090

---

## 九、这样实现的优缺点

### 优点

| 优点 | 说明 |
|---|---|
| **双路径冗余** | Celery 和 Kafka 两条路径可以互相替代，提高系统可用性，一条故障时可切换到另一条 |
| **原生定时调度** | Celery Beat 实现无需外接调度器的定时任务，配置集中管理 |
| **异步解耦** | Kafka 实现生产与消费解耦，生成和推送可以独立扩缩容 |
| **配置外置** | 全部通过 `.env` 配置，环境适配灵活 |
| **订阅号专项适配** | 针对订阅号约束做了专项适配（无模板消息、配额管理、48h 窗口预检） |
| **草稿降级机制** | 配额耗尽时自动降级为"只存草稿"，由人工在公众号后台群发 |

### 缺点

| 缺点 | 说明 |
|---|---|
| **双路径维护成本** | 两套并行路径逻辑重复，修改行为需要同步改两处，容易遗漏 |
| **异步方案混用** | Celery 任务中用 `asyncio.new_event_loop()` 同步调用 async 方法，模式不优雅，改造时应改为真异步 Celery 任务 |
| **爬虫依赖第三方** | 爬虫依赖百度页面结构，页面改版容易导致解析失败（已有 mock 回退） |
| **定时精度有限** | Celery Beat 适合低精度定时任务，高精度场景需要独立调度器 |
| **无自动重试机制** | Kafka 消费者或 Celery 任务失败后的重试策略需要手动配置 |
| **数据库迁移不便** | `init_db()` 已废弃（`app/core/database.py:75-82` 为空函数 + 警告），schema 统一走 Alembic；改动 model 后需生成并执行迁移 |
| **用户触达依赖互动** | 订阅号无法使用模板消息，只能依赖客服消息，用户必须 48h 内有过互动才能收到通知 |

---

## 十、关键文件索引

```
app/
├── main.py                  # FastAPI 入口，lifespan 初始化 DB + Redis
├── core/
│   ├── config.py            # Pydantic Settings，从 .env 读取
│   ├── database.py           # SQLAlchemy 引擎、SessionLocal、Base、get_db()
│   ├── redis_client.py      # Redis 客户端 + 分布式锁
│   ├── jwt_auth.py          # JWT 签发 / 校验
│   ├── auth.py              # require_admin_auth（JWT 优先，X-Admin-Token 兜底）
│   └── rate_limit.py        # slowapi 限流器
├── models/
│   ├── topic.py              # HotTopic、UserChoice
│   ├── article.py            # GeneratedArticle
│   ├── user.py               # UserSubscription
│   ├── admin_user.py         # AdminUser
│   ├── ip_whitelist.py       # IPWhitelist
│   ├── platform_ip_whitelist.py # PlatformIPWhitelist
│   ├── llm_provider.py       # LLMProvider
│   ├── platform.py           # Platform、PlatformAccount、PlatformType
│   └── system_setting.py     # SystemSetting
├── services/
│   ├── crawler.py            # BaiduHotCrawler
│   ├── wechat.py             # WeChatService（订阅号版）
│   ├── llm_clients/          # 多 LLM 供应商客户端（deepseek/openai/minimax/qwen）
│   ├── llm_provider.py       # LLM 供应商配置读写 + API Key 加解密
│   ├── daily_digest.py      # 每日热搜早报组装
│   ├── platforms/            # 多平台 Service（wechat/douyin/xiaohongshu/toutiao/youtube/baijiahao）
│   ├── article_publishing.py # 统一发布 Service（微信 + 多平台）
│   └── article_generation.py # 统一生成 Service
├── api/v1/
│   ├── topics.py             # 话题 CRUD + /crawl /push/choice
│   ├── articles.py           # 文章 CRUD + /generate /preview /{id}/publish
│   ├── webhook.py            # 微信回调接口
│   ├── auth.py               # 登录 / JWT / 用户管理
│   ├── platforms.py          # 平台与平台账号管理
│   ├── llm_providers.py      # LLM 供应商管理
│   └── platform_ip_whitelist.py # 平台 IP 白名单
└── tasks/
    ├── celery_app.py         # Celery 实例 + beat_schedule（含 per-account 动态调度）
    ├── crawl_tasks.py        # crawl_hot_topics / crawl_for_account
    ├── article_tasks.py      # generate_article / publish_article / process_user_choices / daily_publish_digest
    └── push_tasks.py         # push_topics_to_users / push_for_account

consumers/
├── generate_consumer.py     # Kafka 生成消费者
└── push_consumer.py         # Kafka 推送消费者

alembic/versions/             # 数据库迁移脚本
docker-compose.yml            # 服务编排
.env.example                  # 环境变量模板
requirements.txt              # Python 依赖
```
