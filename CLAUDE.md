# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

`wechat-ai-publisher` 是一个基于 AI 自动化的多平台文章发布系统：定时爬取百度热搜 → 通过客服消息推送给用户（订阅号无模板消息权限，另有 09:30 群发日报覆盖全部粉丝）→ 用户选择话题 → DeepSeek 生成文章 → 发布到微信公众号及其他平台。

技术栈：FastAPI + SQLAlchemy 2 + Pydantic v2 + PostgreSQL + Redis + Kafka + Celery + DeepSeek API + 微信公众平台 API + Vue 3 管理后台。整体由 docker compose 编排。

## 常用命令

### 部署与启动（docker compose 是首选方式）

所有服务都是 docker compose 服务，共 15 个：`wechat-postgres`、`wechat-redis`、`wechat-zookeeper`、`wechat-kafka`、`wechat-kafka-ui`、`wechat-migrate`（一次性迁移）、`wechat-app`、`wechat-celery-worker`、`wechat-celery-beat`、`wechat-consumer-generate`、`wechat-consumer-push`、`wechat-consumer-publish`、`wechat-consumer-toutiao-publish`、`wechat-build-web` 与 `wechat-nginx`（后两者在 `with-nginx` profile 下启用）。

```bash
# 初始化环境配置（首次）
cp .env.example .env
# 编辑 .env：必须设置 SECRET_KEY / ADMIN_API_KEY / PG_PASSWORD / REDIS_PASSWORD / DEEPSEEK_API_KEY / WECHAT_APP_ID / WECHAT_APP_SECRET 等

# 一键启动所有服务
bash scripts/deploy.sh
# 等价：docker compose up -d

# 启动并包含管理后台 + Nginx（自动 npm build 前端）
bash scripts/deploy.sh --web
# 等价：docker compose --profile with-nginx up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f [service_name]   # 如 app / celery-worker / consumer-generate

# 停止
docker compose down
```

服务访问入口：
- API: http://localhost:8888
- API 文档 (Swagger): http://localhost:8888/docs
- Kafka UI: http://localhost:9090
- 管理后台: http://localhost （需 `--web` / `with-nginx` profile；用户名 `admin`，密码取自 `.env` 的 `ADMIN_DEFAULT_PASSWORD`，默认 `admin123` 仅限开发环境）

### 本地开发（脱离 docker）

```bash
pip install -r requirements.txt

# 启动 API（需本机可达 postgres/redis/kafka，或在 .env 中覆盖地址）
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload

# 启动 Celery worker（在另一终端，监听所有队列）
celery -A app.tasks.celery_app worker --loglevel=info \
  --concurrency=4 --pool=threads \
  --queues=crawl,article,push,default

# 启动 Celery beat 调度器（另起终端）
celery -A app.tasks.celery_app beat --loglevel=info

# 启动 Kafka 消费者（与 Celery worker 是两套并行的处理路径）
python -m consumers.generate_consumer
python -m consumers.push_consumer
```

### 测试

```bash
# 先装依赖（仓库未内置 venv，缺 celery / confluent-kafka 时 pytest 会直接 collection error）
pip install -r requirements.txt

# 全量测试（pytest 已在 requirements 中）
pytest
make test              # 等价于 python -m pytest -q
make test-cov          # 带覆盖率
make test-docker       # 在容器内跑（Dockerfile 已 COPY tests/）

# 单个测试文件
pytest tests/test_api.py

# 单个测试函数
pytest tests/test_api.py::test_name -v

# 带覆盖率
pytest --cov=app --cov-report=term-missing
```

当前基线：`75 passed / 3 skipped`（无需 PG / Redis / Kafka / 微信凭证）。

### 数据库迁移 (Alembic)

```bash
# 自动生成迁移（修改了 model 之后）
alembic revision --autogenerate -m "add xxx column"

# 应用所有未执行的迁移
alembic upgrade head

# 回滚一步
alembic downgrade -1

# 查看当前版本
alembic current

# 查看迁移历史
alembic history --verbose

# 离线生成 SQL（不连库）
alembic upgrade head --sql > migrate.sql
```

部署路径：通过 `docker-compose.yml` 的 `migrate` 一次性服务，所有其他服务 `depends_on: service_completed_successfully`。本机直接 `alembic upgrade head` 即可。

### Celery beat 持久化

`celery-beat` 用 `-s /var/lib/celerybeat/celerybeat-schedule`，该目录已挂载到 `celerybeat_data` 命名 volume，beat 重启不会丢失调度进度。

### 定时任务（beat_schedule）

`app/tasks/celery_app.py:_build_beat_schedule()` 在 Celery 启动时动态生成：

| 任务 | 时间 | 队列 |
|---|---|---|
| `crawl-hot-topics` | 每天 08:00（`SCHEDULER_CRAWL_TIME`） | crawl |
| `push-topics-to-users` | 每天 09:00（`SCHEDULER_PUSH_TIME`，已弃用但保留） | push |
| `daily-publish-digest` | 每天 09:30（群发日报，覆盖全部粉丝） | article |
| `process-user-choices` | 每 30 分钟 | article |

此外 `_add_per_account_schedules()` 会从 `platform_accounts` 表读取各账号的 `crawl_cron` / `push_cron`，动态注入 `crawl-{account_id}` / `push-{account_id}` 两个任务；账号增删改后可调 `reload_beat_schedule()` 重载。

### 字段命名：`metadata` ↔ `extra_metadata`

`hot_topics` 和 `generated_articles` 各有一列存任意 KV 配置，按 SQLAlchemy 规范类属性叫 `extra_metadata`，但 ORM attribute 与底层列名不能同名（`Base.metadata` 是保留属性），所以 `Column("metadata", JSONB, ...)`：

| 层级 | 名字 |
|---|---|
| 数据库列名 | `metadata` |
| Python ORM 属性 | `extra_metadata` |
| Pydantic schema | `extra_metadata` |
| API JSON | `extra_metadata` |

读写例子：

```python
topic.extra_metadata = {"source": "wechat"}      # ORM 写法
db.refresh(topic)
row = db.execute(text("SELECT metadata FROM hot_topics WHERE id=:i"), {"i": 1}).first()
row[0]  # 是 raw jsonb 内容
```

Schema 里仍然用 `extra_metadata`（一致），不要写 `metadata` 否则会得到 Pydantic 配置错误。

### WeChatService 现已全部同步

旧版 `async def` + 内部 `requests` 阻塞 → Celery 任务用 `asyncio.new_event_loop()` 把 async 函数同步跑，模式 buggy。现全部改成同步 `def`，Celery / consumer / FastAPI 都能直接调用。`RedisClient.run_sync()` 提供同步侧访问 async Redis 客户端的能力（少量调用 OK，高并发可换 `redis.Redis()` 同步客户端）。

测试用例已补齐：`tests/__init__.py`、`tests/conftest.py`（pytest fixture）、`tests/test_api.py`、`tests/test_services.py`、`tests/test_wechat.py`。

## 代码架构

### 目录结构（实际存在的文件）

```
app/
  main.py                  FastAPI 入口，lifespan 初始化 DB + Redis，挂载路由 + 全局异常处理
  api/
    deps.py                公共依赖（DB session、鉴权等）
    v1/
      auth.py              管理员登录 / refresh token / 修改密码
      ip_whitelist.py      全局访问控制白名单 CRUD
      settings.py          系统设置（system_settings 表）读写
      keyword_articles.py  关键词深度文章生成（管理后台手动触发）
      platforms.py         平台账号（PlatformAccount）CRUD + 各平台凭据管理
      llm_providers.py     LLM 模型供应商配置 CRUD
      platform_ip_whitelist.py  各平台独立的 IP 白名单
      tasks.py             手动触发 Celery 任务（爬取 / 生成 / 发布 / 日报）
      topics.py            话题 CRUD + POST /crawl /push/choice（choice 会把 HotTopic 状态置为 SELECTED）
      articles.py          文章 CRUD + POST /generate（异步） / preview（同步调用 LLM）/ {id}/publish
      webhook.py           微信回调（subscribe / click / text），刷新 last_interact_time
  core/
    config.py              Pydantic Settings，从 .env 读取（必填字段见下方「配置」小节）
    database.py            SQLAlchemy 引擎、SessionLocal、Base、get_db()、session_scope()、init_db()（已废弃）
    auth.py                管理后台 token（X-Admin-Token）校验
    jwt_auth.py            JWT access/refresh token 签发与校验
    rate_limit.py          限流（Redis 计数）
    circuit_breaker.py     熔断器（DeepSeek / WeChat / 爬虫共用）
    pii.py                 PII 脱敏（openid HMAC 散列，日志用）
    security.py            密码哈希、Fernet 加密等安全工具
    redis_client.py        Redis 客户端（async + run_sync 同步侧访问）
    runtime_overrides.py   把管理后台 system_settings 覆盖到运行时 settings（白名单 + TTL 缓存）
    kafka_client.py        Kafka topic 初始化（init_topics）
    logging.py             日志配置（标准库 logging 封装）
  models/
    topic.py               HotTopic（PENDING/PUSHED/SELECTED/PROCESSED/FAILED）、UserChoice
    article.py             GeneratedArticle（GENERATING/GENERATED/PUBLISHING/PUBLISHED/DRAFT/FAILED）
    admin_user.py          AdminUser（管理后台账号）
    ip_whitelist.py        IpWhitelist（全局白名单）
    platform.py            Platform（平台定义）、PlatformAccount（平台账号 + 凭据 + 独立 cron）
    platform_ip_whitelist.py   PlatformIpWhitelist
    llm_provider.py        LlmProvider（LLM 供应商配置）
    system_setting.py      SystemSetting（键值对系统设置）
    user.py                User（关注用户，last_interact_time 用于 48h 窗口）
  schemas/                 与 models 对应的 Pydantic v2 Schema（topic/article/auth/platform/llm_provider/…）
  services/
    crawler.py             BaiduHotCrawler：requests 调百度热搜 JSON 接口（top.baidu.com/api/board）+ 二次跳解析真实新闻页，失败回退到 _get_mock_data()
    wechat_core.py         WeChatApiClient：**微信 HTTP 语义的唯一实现**（access_token / 客服消息 / 草稿 / 群发 / 封面素材上传 + 熔断器 + 40101 诊断）。与凭据来源无关
    wechat.py              WeChatService：``.env`` 凭据入口，是 `wechat_core.WeChatApiClient` 的薄适配器（send_custom_message / create_draft / publish_draft）
    wechat_publish.py      统一的「建草稿 → 群发」流程 + 封面解析 + 按账号维度的配额统计（publish / publish_to_platform / daily_publish_digest 三处共用）
    article_generation.py  ArticleGenerationService：话题 → 文章的完整生成流程
    article_publishing.py  ArticlePublishingService：多平台发布编排（配额预检 → 存草稿 → 群发）
    daily_digest.py        DailyDigestService：每日热搜日报渲染（markdown / HTML）
    llm_provider.py        LLM 供应商解析与调用入口
    push_notification.py   推送通知封装
    kafka_producer.py      Kafka 生产者封装
    platforms/             多平台发布（工厂模式，详见下方「多平台发布」）
      __init__.py          PlatformServiceFactory：按 platform_type 返回服务实现（已注册 6 个平台）
      base.py              PlatformService 抽象基类、PublishResult、CrawledTopic
      crawlers.py          BaseCrawler + 各平台爬虫（抖音/小红书/百家号/头条/YouTube/微信）+ CrawlerFactory
      wechat_service.py    WeChatPlatformService
      douyin_service.py    DouyinPlatformService
      xiaohongshu_service.py  XiaohongshuPlatformService
      toutiao_service.py   ToutiaoPlatformService
      baijiahao_service.py BaijiahaoPlatformService
      youtube_service.py   YouTubePlatformService
    llm_clients/           LLM 客户端抽象
      base.py              LLMClient 抽象基类
      deepseek.py          DeepSeek 客户端
      openai.py            OpenAI 兼容客户端
      minimax.py           MiniMax 客户端
      qwen.py              通义千问客户端
  utils/
    image.py               图片下载 / 裁剪（封面 2.35:1）
    markdown.py            Markdown → 微信内联 HTML
  tasks/
    celery_app.py          Celery 实例 + beat_schedule（见下方「定时任务」）+ task_routes（crawl/article/push 三队列）
    crawl_tasks.py         crawl_hot_topics / crawl_for_account：调用爬虫，按 title+date 去重写入 hot_topics
    article_tasks.py       generate_article / publish_article / daily_publish_digest / process_user_choices / generate_deep_article
    push_tasks.py          push_topics_to_users / push_for_account（客服消息，48h 窗口预检）
consumers/
  generate_consumer.py     Kafka 订阅 kafka_topic_generate，调用 LLM 服务，发送 push-notifications / publish-articles 到 Kafka
  push_consumer.py         Kafka 订阅 kafka_topic_push，调用 send_custom_message（订阅号 48h 窗口预检）
  publish_consumer.py      Kafka 订阅 kafka_topic_publish，执行多平台发布（含配额预检）
  toutiao_publish_consumer.py  头条平台专用发布消费
  _dlq.py                  DeadLetterHandler：重试计数与死信队列处理
alembic/
  env.py                   Alembic 环境（读取 app.core.config）
  script.py.mako           迁移脚本模板
  versions/                12 个迁移文件（日期前缀命名，见下方「已知缺口」）
web/                       Vue 3 + TypeScript + Vite 管理后台
  src/
    views/                 14 个视图页面（Dashboard / PlatformAccounts / WeChatConfig / DeepSeekConfig /
                           LLMProviders / SchedulerConfig / IPWhitelist / PlatformIpWhitelist /
                           KeywordArticleBuilder / HotReportBuilder / ToutiaoHotTopics /
                           UserManagement / Profile / Login）；连同 App.vue 与 components/ 共 19 个 .vue
    components/            MainLayout / AccountCard / AccountFormDialog / ConfigForm
    api/                    9 个 API 模块（auth / client / articles / platforms / llmProviders /
                           ipWhitelist / platformIpWhitelist / keywordArticles / settings）
    router/index.ts         路由（hash 模式）
    stores/user.ts          Pinia 用户 / token 状态
    utils/jwt.ts            token 存取
docker/                    postgres 初始 SQL（init.sql/ 目录）+ postgresql.conf、nginx 配置
scripts/                    deploy.sh（支持 --web）/ entrypoint.sh / backup.sh / 各类运维脚本
docs/                       DATA_FLOW / FLOW / OPERATIONS / PLATFORM_INTEGRATION 等文档
tests/                      conftest.py + test_api.py + test_services.py + test_wechat.py
.env.example                环境变量模板（基础/PG/Redis/Kafka/Celery/DeepSeek/微信/调度/功能开关/Nginx/Worker）
docker-compose.yml          全部服务的编排（15 个服务）
requirements.txt            依赖清单（FastAPI 0.104、SQLAlchemy 2.0、confluent-kafka 2.5、Celery 5.3、loguru、httpx 等）
tree.txt                    项目目录结构示意
start.txt                   scripts/deploy.sh 的说明副本（以脚本为准）
deploy.md                   部署指南（Markdown）
```

### 多平台发布

`app/services/platforms/` 采用工厂模式：平台定义与凭据落在 `platform` / `platform_accounts` 表，发布时由 `PlatformServiceFactory.get_service(account)` 按 `platform_type` 选出实现类。

- `PlatformServiceFactory._services` 已注册 6 个平台：`wechat`、`douyin`、`xiaohongshu`、`toutiao`、`youtube`、`baijiahao`。
- 新增平台：继承 `app/services/platforms/base.py` 的 `PlatformService`，实现 publish / 草稿 / 凭据校验接口，再在 `PlatformServiceFactory` 里 `register()` 即可。
- 爬虫侧对称：`app/services/platforms/crawlers.py` 里的 `CrawlerFactory` 提供各平台热榜爬取。
- 每个平台账号可单独配置 `crawl_cron` / `push_cron`，Celery beat 在启动时动态注入 `crawl-{account_id}` / `push-{account_id}` 任务（`app/tasks/celery_app.py:_add_per_account_schedules`）。
- 发布流程统一走 `app/services/article_publishing.py:ArticlePublishingService`，微信侧仍受 `WECHAT_DAILY_PUBLISH_QUOTA` 限制。
- 微信的「建草稿 → 群发」细节统一在 `app/services/wechat_publish.py:publish_wechat_draft`；`publish()`（`.env` 凭据）与 `publish_to_platform()`（微信平台账号）走的是同一份实现，差别只有凭据来源与配额统计维度（按 `platform_account_id` 隔离）。

### 数据流

```
        BaiduHotCrawler                Kafka                  DeepSeek                WeChatService
             │                       (crawl-topics)                │                       │
             ▼                           │                          ▼                       ▼
      crawl_hot_topics                    │              generate_article /              send_custom_message
      (Celery @08:00)                     │              generate_consumer                push_consumer
             │                           │                          │                       │
             ▼                           ▼                          ▼                       ▼
       ┌─────────────────────────────────────────────────────────────────────────────────────┐
       │  PostgreSQL:  hot_topics ── user_choices ── generated_articles ── (wechat_media_id)  │
       └─────────────────────────────────────────────────────────────────────────────────────┘
             ▲                           ▲                          ▲                       ▲
   FastAPI /api/v1/topics          /api/v1/articles          publish_article          access_token
                                                                   │
                                                            Redis (wechat:access_token:{appid} 缓存)
```

要点：
- 同一份业务有 **两套并行执行路径**：Celery 任务（`app/tasks/*`）与 Kafka 消费者（`consumers/*`）。它们各自能完成「生成文章」「通知用户」的功能 —— 修改行为时须确认是哪条路径被触发，否则容易只改一处。FastAPI 端点目前默认走 Celery 路径（`.delay()`）。
- Kafka 话题：`crawl-topics` / `generate-articles` / `push-notifications` / `publish-articles`（命名见 `.env.example` 的 `KAFKA_TOPIC_*`）。
- Celery 队列：`crawl` / `article` / `push` / `default`，与 `task_routes` 一一对应。
- 数据库表：schema 走 Alembic 迁移，`init_db()` 已废弃（`app/core/database.py:75-82`，只打 warning 不建表）。compose 里 `wechat-migrate` 一次性服务执行 `alembic upgrade heads`，其余服务 `depends_on: service_completed_successfully`。
- 配置：`app/core/config.py` 中标 `Field(...)`（无默认值）的是必填项，缺失对应环境变量会启动失败。当前共 11 个必填项：`SECRET_KEY`、`ADMIN_API_KEY`（≥16 字符）、`PG_USER`、`PG_PASSWORD`、`REDIS_PASSWORD`、`CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`、`DEEPSEEK_API_KEY`、`WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`WECHAT_TOKEN`；`celery_broker_url` / `celery_result_backend` 若以 `redis://` 开头会原样使用，否则由 redis 配置自动拼接。
- 时区：`TIMEZONE`（默认 `Asia/Shanghai`）是唯一时区配置项，`CELERY_TIMEZONE` 已并入（`Settings.sync_timezone` 自动同步），Celery 与日报分组都用它。

### 添加 / 修改功能的入口指引

| 想做的事 | 改哪里 |
|---|---|
| 新增 HTTP 接口 | `app/api/v1/*.py`，在 `app/main.py` 中 `include_router` |
| 改数据库表 | `app/models/*.py` + `alembic revision --autogenerate` + `alembic upgrade head`（compose 下由 `wechat-migrate` 服务执行） |
| 加定时任务 | `app/tasks/celery_app.py` 的 `beat_schedule` + 在 `app/tasks/*_tasks.py` 实现并 `include` 进 `celery_app` |
| 改爬虫 | `app/services/crawler.py` |
| 改 AI 生成 | `app/services/llm_clients/`（base/deepseek/openai/minimax/qwen，统一的 `LLMClientBase` 接口）+ `app/services/llm_provider.py`（供应商配置解析）；完整生成流程见 `app/services/article_generation.py` |
| 加 / 改发布平台 | `app/services/platforms/`（继承 `base.py` 的 `PlatformService`，在 `PlatformServiceFactory` 注册）+ `app/services/platforms/crawlers.py` |
| 改微信 API 调用 | `app/services/wechat_core.py`（唯一实现：access_token 缓存 key 是 `wechat:access_token:{appid}`；发布配额见 `WECHAT_DAILY_PUBLISH_QUOTA`）。改完之后 `app/services/wechat.py` 与 `app/services/platforms/wechat_service.py` 两个适配器自动生效 |
| 加 Kafka 处理 | `consumers/*.py` 模板一致：建类 → `KafkaConsumer(...)` → `for message in self.consumer` 处理；新增 container 在 `docker-compose.yml` |
| 加环境变量 | `.env.example` + `app/core/config.py` 的 `Settings` 字段 |
| 让管理后台的配置项生效 | `app/core/runtime_overrides.py` 的 `_OVERRIDABLE` 白名单（只列运行时真会被读到的键） |

### 已知缺口（与 `tree.txt` 不一致 / 已完成）

`tree.txt` 已按实际文件系统重新生成，下面的记录是历史沿革：

仓库已补全到可启动 + 已迁移至 Alembic：

- 所有原本 `tree.txt` 列出但缺文件的模块均已补齐
- 修 2 个原始 bug：`AsyncAdaptedQueuePool → QueuePool`；`metadata` 字段改名为 `extra_metadata`（DB 列名不变）
- DB schema 现在走 Alembic，`init_db()` 改成空函数 + 警告。`alembic/versions/` 下共 12 个迁移文件，日期前缀命名：
  `2026_07_02_0000-0001_initial_schema.py`、`2026_07_02_0001-0002_add_fk_and_unique_constraint.py`、`2026_07_02_0002-0003_user_choice_unique_constraint.py`、`2026_07_24_0000-0004_admin_config_tables.py`、`2026_07_27_1053-5861f005e42d_sync_model_defaults_and_indexes_with_.py`、`2026_08_10_0000-9916a663457e_add_platform_accounts.py`、`2026_08_12_0000-a1b2c3d4e5f6_seed_all_platforms.py`、`2026_08_13_0000_fix_baijiahao_typo.py`、`2026_08_13_0001_migrate_keyword_hash_to_sha256.py`、`2026_08_14_0000_create_llm_providers.py`、`2026_10_12_0000-create_platform_ip_whitelist.py`、`2026_10_12_0001_merge_heads.py`、`2026_11_02_0000_remove_dead_system_settings.py`（以 `ls alembic/versions` 为准）
- docker compose 引入 `migrate` 一次性服务，所有业务容器 `depends_on: service_completed_successfully`
- Celery beat 的 `/var/lib/celerybeat/` 挂载到 `celerybeat_data` volume，重启不丢进度

如果将来要恢复 `metadata` 这个字段名，需要先看 `Base` 的 `cls.metadata` 冲突 — SQLAlchemy 不允许声明式模型使用这个名字。

仓库已补全到可启动：所有原本在 `tree.txt` 中列出但缺文件的模块（`redis_client.py`、`kafka_client.py`、`logging.py`、`kafka_producer.py`、`push_tasks.py`、`webhook.py`、`deps.py`、`user.py`、`Dockerfile`、三个 `scripts/*.sh`、两个 `tests/*.py`、`README.md`）均已补齐，并修了 2 处原有 bug：

1. `app/core/database.py` 把同步 `create_engine` 的 `poolclass` 由 `AsyncAdaptedQueuePool`（只适用于 `create_async_engine`）改成 `QueuePool`。
2. `app/models/topic.py` 与 `app/models/article.py` 原先的 `metadata = Column(JSONB, …)` 与 SQLAlchemy `Base.metadata` 保留属性冲突，现已改名为列属性 `extra_metadata`（数据库列名仍叫 `metadata`，通过 `Column("metadata", …)` 兼容）。对应的 `app/schemas/topic.py` / `article.py` Schema 字段也同步改为 `extra_metadata`。

### 当前遗留问题

- ~~**两套微信实现并存**~~ → **已收敛**（本轮）。微信 HTTP 语义只剩一份实现 `app/services/wechat_core.py:WeChatApiClient`；两个入口都退化成薄适配器：`.env` 凭据走 `app/services/wechat.py:WeChatService`，`platform_accounts` 凭据走 `app/services/platforms/wechat_service.py:WeChatPlatformService`。「建草稿 → 群发」也只剩一份实现 `app/services/wechat_publish.py:publish_wechat_draft`，由 `ArticlePublishingService.publish()`、`ArticlePublishingService.publish_to_platform()`（微信分支）、`daily_publish_digest` 三处共用。**改微信行为只需要改 `wechat_core.py` / `wechat_publish.py`。**
- **微信凭据仍有两处来源**：`.env`（`settings.wechat_app_id/secret`，供 `.env` 路径）与 `platform_accounts` 表（供多账号路径）。二者现在共用同一客户端，但**仍是两套配置**；管理后台「平台账号管理」配的是后者，「系统配置」页配的是前者（见下一条）。
- **管理后台 system_settings 已接回运行时**（本轮修复）。此前 `app/api/v1/settings.py:_get_runtime_value()` 定义了却从无调用点，页面上改什么都不生效。现在由 `app/core/runtime_overrides.py:apply_runtime_overrides()` 把表里的值覆盖到 `settings` 单例上（进程启动时 + 每个 Celery 任务 / 每条 Kafka 消息前，带 15s TTL 缓存；管理后台保存后 `force=True` 立即重算）。只覆盖 `_OVERRIDABLE` 白名单里的键 —— 凭证类（`wechat_app_id/secret`）留空不覆盖、LLM 类（`deepseek_*`）已由 `llm_providers` 表接管，因此 `2026_11_02_0000` 迁移删掉了 6 行死配置。
- **已删除的死代码**：`app/services/crawler1.py`（空文件）、`app/services/crawler_bak.py`（旧 HTML 解析版爬虫备份）、`app/services/deepseek.py`（已被 `app/services/llm_clients/` 取代；宽松 JSON 解析迁到 `LLMClientBase._parse_response`，`tests/test_services.py` 已改指 `DeepSeekClient`）。
- **测试基线**：`pytest` 当前 75 passed / 3 skipped（新增 `tests/test_wechat.py`，28 个用例覆盖微信客户端与统一发布流程）。后续再改 `DailyDigestService.build_digest`、`_is_real_news_url` 或 `wechat_core.py` 时记得同步对应测试文件。

### 开发注意事项

- 容器内的 API 启动命令会先调用 `init_db()` 与 `init_topics()`，所以本地 `uvicorn app.main:app` 起服务时不会执行 Kafka 初始化；只有 docker / `scripts/entrypoint.sh app` 路径会。
- `WeChatService` 已是全同步 `def`，`publish_article` 直接委托 `ArticlePublishingService.publish()`（`app/tasks/article_tasks.py:58-68`），不再有 `asyncio.new_event_loop()` 的历史包袱。
- 微信的 Redis key 现在都带公众号维度（`wechat:access_token:{appid}` / `wechat:default_cover_media_id:{appid}` / `wechat:cover_media_id:{appid}:{sha1}`）——旧的无前缀 key 不会被读，升级后首次调用会重新取一次 token。
- 调试环境变量时区分两层：`docker-compose.yml` 的 `app-env` YAML 锚点会注入所有容器，`.env` 用于补充未在 YAML 中显式声明的变量。
- 仓库根目录曾经有游离的 `models/` 与 `services/`（与 `app/` 下重复）以及一个错误的 `app/schemas/init.py`，已清理。

### 订阅号约束（区别于服务号）

本项目运行在**订阅号**上，公众号后台的"账号类型"是订阅号（非服务号），适配时需注意以下硬性限制：

1. **没有模板消息**：订阅号无 `template/send` 接口权限，调用 `send_template_message` 会被微信 `errcode=43004` 拒绝。本项目代码里已**完全删除**模板消息相关代码（`send_template_message` 方法、`WeChatTemplateMessage` schema、`WECHAT_TEMPLATE_ID_*` 配置）。所有主动通知统一走 `send_custom_message`（客服消息，`app/services/wechat.py:129`）；同时每天 09:30 由 `daily_publish_digest` 群发一篇日报覆盖全部粉丝（`app/tasks/celery_app.py:93`）。
2. **每日群发配额**（`freepublish/submit` 调用次数）：
   - 个人订阅号：1 篇/天
   - 认证订阅号 / 服务号：4 篇/天
   - 通过 `.env` 的 `WECHAT_DAILY_PUBLISH_QUOTA` 配置（默认 1）。
   - 配额预检统一在 `app/services/article_publishing.py:ArticlePublishingService.publish()` 入口处（`app/tasks/article_tasks.py:publish_article` 与 `consumers/publish_consumer.py:process_message` 都委托给它），耗尽时把多余文章置 DRAFT 不调微信 API。
3. **客服消息 48h 窗口**：`send_custom_message` 只能发给 48h 内互动过的用户，否则 `errcode=45015`。
   - `app/tasks/push_tasks.py:push_topics_to_users` 在 SQL 里预过滤 `last_interact_time >= now-48h`。
   - `consumers/push_consumer.py:process_message` 在发之前单条查 `last_interact_time`，不通过就跳过并记 `push_skipped_48h_window` 事件。
   - `app/api/v1/webhook.py` 在 3 处（subscribe / click / text 消息）调用 `_touch_interact` 刷新 `last_interact_time`，确保活跃用户能被 48h 预检命中。
4. **草稿/发布**：`draft/add` 不受配额限制，可任意调用；`freepublish/submit` 受配额限制。系统默认流程是「先存草稿 → 立即群发」，配额耗尽时退化为「只存草稿」由人工在公众号后台群发。
5. **网页授权**：订阅号只能用 `snsapi_userinfo`（弹出授权），不能用 `snsapi_base`（静默授权）。本项目目前未使用网页授权，无需处理。

