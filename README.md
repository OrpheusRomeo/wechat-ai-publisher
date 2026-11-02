# 微信 AI 文章发布系统 (wechat-ai-publisher)

基于 **FastAPI + Celery + Kafka + Vue 3** 的多平台 AI 内容自动生产与发布系统。

核心流程：**爬取热搜 → AI 生成文章 → 多平台发布（微信公众号 / 今日头条 / 百家号 / 抖音 / 小红书 / YouTube）**，配套 Vue 3 管理后台完成账号配置、内容审核与发布调度。

> 本项目运行在**微信公众号订阅号**上：订阅号没有模板消息权限，主动触达统一走客服消息（48h 窗口）或群发日报，详见文末「订阅号约束」。

---

## 功能特性

| 模块 | 说明 |
|---|---|
| 热搜爬虫 | 调用百度热搜官方 JSON 接口（`top.baidu.com/api/board`），二次跳解析真实新闻详情页；失败可回退 mock 数据 |
| AI 生成 | 多供应商 LLM 客户端（DeepSeek / OpenAI 兼容 / MiniMax / 通义千问），后台可视化配置与切换 |
| 多平台发布 | 统一 `PlatformService` 抽象，工厂注册 微信 / 今日头条 / 百家号 / 抖音 / 小红书 / YouTube 六个平台 |
| 定时调度 | Celery Beat：定时爬虫、日报群发、用户选题处理；每个平台账号可单独配置 cron |
| 双执行路径 | Celery 任务（`app/tasks/*`）与 Kafka 消费者（`consumers/*`）并行，Kafka 侧带 DLQ 死信队列 |
| 管理后台 | Vue 3 + Element Plus SPA：仪表盘、平台账号、LLM 供应商、系统配置、IP 白名单、用户管理 |
| 安全 | JWT 登录 + 管理员 Token 双认证、IP 白名单（全局 + 分平台）、Fernet 敏感配置加密、全局限流、PII 日志脱敏、平台 IP 绑定 |
| 数据库 | PostgreSQL + SQLAlchemy 2.0 + Alembic 迁移（共 12 个 revision） |

---

## 系统架构

```
        百度热搜 API                  管理后台 (Vue 3 SPA, Nginx)
             │                                │
             ▼                                ▼
      crawl_hot_topics  ──────────►  FastAPI /api/v1/*
      (Celery beat 08:00)           (JWT 鉴权 / IP 白名单 / 限流)
             │                                │
             ▼                                │
        hot_topics  ◄─────────────────────────┘
             │
             ├──► generate_article ──► LLM 供应商 (DeepSeek/MiniMax/Qwen/OpenAI)
             │         │                              │
             │         ▼                              ▼
             │   generated_articles            generated_articles
             │         │
             │         ├──► publish_article ──► PlatformServiceFactory
             │         │        (草稿 → 群发)      ├── WeChatPlatformService
             │         │                          ├── ToutiaoPlatformService
             │         │                          ├── BaijiahaoPlatformService
             │         │                          ├── DouyinPlatformService
             │         │                          ├── XiaohongshuPlatformService
             │         │                          └── YouTubePlatformService
             │         ▼
             └─► daily_publish_digest (09:30 群发日报)

     Kafka 旁路：crawl-topics → generate-articles → publish-articles / push-notifications
                  失败消息重试 KAFKA_CONSUMER_MAX_ATTEMPTS 次后进入 wechat-dlq
```

**存储**：PostgreSQL（业务数据）、Redis（access_token 缓存 / Celery broker / 限流计数 / DLQ 状态）。

---

## 技术栈

- **Web**：FastAPI 0.104 · Pydantic v2 · SQLAlchemy 2.0 · slowapi 限流
- **前端**：Vue 3.4 · Vite 5 · Element Plus 2.6 · Pinia · TypeScript
- **任务队列**：Celery 5.3（worker + beat，队列 `crawl` / `article` / `push` / `default`）
- **消息队列**：Kafka 7.2（Confluent 镜像）+ Kafka UI
- **存储**：PostgreSQL 15 · Redis 7.2
- **AI**：DeepSeek / OpenAI 兼容接口 / MiniMax / 通义千问（`app/services/llm_clients/`）
- **部署**：Docker Compose（可选 Nginx 反代 + SPA 静态托管）· Alembic 迁移

---

## 快速开始

### 1. 环境要求

- Docker Engine + Compose v2（推荐）
- 可选：`./ssl/` 放证书以启用 `with-nginx` profile 的 HTTPS

### 2. 准备配置

```bash
cp .env.example .env
```

**必须修改**的 `.env` 项：

| 变量 | 说明 |
|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` |
| `PG_PASSWORD` / `REDIS_PASSWORD` | 数据库 / 中间件密码 |
| `ADMIN_API_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `PII_LOG_SALT` | 至少 8 位随机串，用于 openid 散列 |
| `FERNET_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com 申请 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` / `WECHAT_TOKEN` | 微信公众平台后台 |

### 3. 启动

```bash
bash scripts/deploy.sh          # 等价于 docker compose up -d
```

首次启动时 `migrate` 服务会自动执行 Alembic 迁移，其他业务容器等它成功后再启动。

### 4. 访问入口

| 服务 | 地址 |
|---|---|
| 管理后台 | http://localhost （启用 `with-nginx` profile） |
| API | http://localhost:8888 |
| Swagger | http://localhost:8888/docs |
| ReDoc | http://localhost:8888/redoc |
| Kafka UI | http://localhost:9090 |

默认管理员由迁移 `0004_admin_config_tables` 写入：**`admin` / `admin123`**。
登录后请立即在「个人资料 → 修改密码」里改掉；`ADMIN_DEFAULT_PASSWORD` 保持默认时会持续输出安全警告。

启用 Nginx：

```bash
docker compose --profile with-nginx up -d
```

### 5. 前端开发（可选）

```bash
cd web
npm install
npm run dev        # http://localhost:5173
npm run build      # 产物输出到 web/dist，由 build-web 服务打包进 nginx 镜像
```

---

## 目录结构

```
app/
  main.py                FastAPI 入口，路由注册 / CORS / 限流 / 全局异常处理
  core/                  config · database · redis_client · kafka_client · logging
                         security · auth · jwt_auth · rate_limit · circuit_breaker · pii
  models/                topic · article · user · admin_user · platform · llm_provider
                         system_setting · ip_whitelist · platform_ip_whitelist
  schemas/               对应 Pydantic v2 Schema（extra_metadata 命名见下）
  services/              crawler · article_generation · article_publishing
                         daily_digest · kafka_producer · llm_provider · wechat
                         push_notification · markdown · image
    llm_clients/         base · deepseek · openai · minimax · qwen
    platforms/           base · wechat · toutiao · baijiahao · douyin · xiaohongshu
                         · youtube · crawlers
  api/v1/                topics · articles · keyword_articles · platforms · auth
                         settings · tasks · llm_providers · ip_whitelist
                         platform_ip_whitelist · webhook
  tasks/                 celery_app · crawl_tasks · article_tasks · push_tasks
consumers/               generate · publish · push · toutiao_publish · _dlq
web/                     Vue 3 管理后台（src/ · dist/）
alembic/                 数据库迁移（versions/ 共 12 个 revision）
docker/                  postgres · redis · nginx 配置
scripts/                 deploy · manage · backup · build_web · db_migrate · smoke_test …
docs/                    架构与运维文档（见文末索引）
tests/                   pytest 单元与冒烟测试
```

---

## 核心配置说明

配置全部通过 `app/core/config.py`（Pydantic Settings）从 `.env` 读取，分组如下：

| 分组 | 关键变量 |
|---|---|
| 基础 | `APP_ENV`（production 严格走 IP 白名单）、`SECRET_KEY`、`TIMEZONE`、`ADMIN_API_KEY`、`PII_LOG_SALT`、`FERNET_KEY`、`JWT_SECRET_KEY`、`CORS_ALLOWED_ORIGINS` |
| PostgreSQL | `PG_HOST/PORT/USER/PASSWORD/DB`、`PG_POOL_SIZE`、`PG_MAX_OVERFLOW` |
| Redis | `REDIS_HOST/PORT/PASSWORD`、DB 分库：`0` 业务 / `1` Celery / `2` 缓存 |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS`、topic `crawl-topics` / `generate-articles` / `push-notifications` / `publish-articles` / `wechat-dlq` |
| Celery | `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`、`CELERY_WORKER_CONCURRENCY`、`CELERY_TASK_TIME_LIMIT` |
| LLM | `DEEPSEEK_API_KEY/URL/MODEL/TEMPERATURE/MAX_TOKENS/TIMEOUT`（其余供应商在后台配置） |
| 微信 | `WECHAT_APP_ID/SECRET/TOKEN`、`WECHAT_DAILY_PUBLISH_QUOTA`、`WECHAT_ENABLE_CUSTOM_MESSAGE`、`WECHAT_DEFAULT_COVER_URL`、`WECHAT_DIGEST_COLOR_*` |
| 调度 | `SCHEDULER_CRAWL_TIME`、`SCHEDULER_PUSH_TIME`、`SCHEDULER_TOPIC_LIMIT`、`BAIDU_HOT_USE_MOCK` |
| 开关 | `ENABLE_CRAWLER` / `ENABLE_WECHAT_PUSH` / `ENABLE_AUTO_PUBLISH` / `ENABLE_DEEPSEEK` / `ENABLE_KAFKA` / `ENABLE_CELERY` |

---

## 定时任务

| 任务 | 默认时间 | 队列 |
|---|---|---|
| `crawl-hot-topics` | `SCHEDULER_CRAWL_TIME`（08:00） | `crawl` |
| `push-topics-to-users` | `SCHEDULER_PUSH_TIME`（09:00） | `push` |
| `daily-publish-digest` | 09:30 | `article` |
| `process-user-choices` | 每 30 分钟 | `article` |
| `crawl-{account_id}` / 各账号自定义 | 平台账号级 cron | `crawl` |

平台账号新增/修改调度后，可调用 `POST /api/v1/tasks/celery/reload-beat` 热加载 Beat schedule。

---

## 主要 API

| 前缀 | 用途 |
|---|---|
| `/api/v1/auth` | 登录、刷新 token、当前用户、用户 CRUD、改密 |
| `/api/v1/topics` | 话题 CRUD、爬取、推送、用户选题 |
| `/api/v1/articles` | 文章 CRUD、生成、预览、发布 |
| `/api/v1/keyword-articles` | 关键词文章（热搜日报构建器） |
| `/api/v1/platforms` | 平台与平台账号 CRUD、调度配置、恢复/永久删除 |
| `/api/v1/llm-providers` | LLM 供应商 CRUD 与连通性测试 |
| `/api/v1/settings` | 系统配置读写（批量更新） |
| `/api/v1/ip-whitelist` | 全局 IP 白名单 |
| `/api/v1/platform-ip-whitelist` | 分平台 IP 白名单 |
| `/api/v1/tasks` | 任务状态查询、Beat 热加载 |
| `/webhook` | 微信回调：关注 / 点击 / 文本消息（刷新 48h 互动窗口） |

---

## 数据库与迁移

```bash
alembic upgrade head                    # 应用全部迁移
alembic revision --autogenerate -m "..." # 修改 model 后自动生成
alembic downgrade -1                    # 回滚一步
alembic current                         # 查看当前版本

bash scripts/db_migrate.sh              # 在容器内执行迁移
bash scripts/db_autogen.sh              # 容器内自动生成迁移
```

> **字段命名注意**：`hot_topics` / `generated_articles` 的任意 KV 列在数据库里叫 `metadata`，
> 但 SQLAlchemy 声明式模型不能占用 `Base.metadata`，因此 ORM / Schema / API 中统一叫 **`extra_metadata`**。

---

## 常用命令

```bash
# Makefile 快捷方式
make help            # 查看全部可用目标
make up / down / ps / logs
make restart-app / restart-consumer
make build-web       # 重建前端并刷新 nginx

# 脚本
bash scripts/manage.sh app logs
bash scripts/manage.sh celery-worker restart
bash scripts/backup.sh                   # 数据库备份
bash scripts/check_publish.sh            # 发布链路自检
bash scripts/e2e_verify.sh               # 端到端验证
bash scripts/smoke_test.sh               # 冒烟测试

# 本地开发（脱离 docker）
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
celery -A app.tasks.celery_app worker --loglevel=info --concurrency=4 --pool=threads \
  --queues=crawl,article,push,default
celery -A app.tasks.celery_app beat --loglevel=info
python -m consumers.generate_consumer
python -m consumers.publish_consumer
python -m consumers.push_consumer
```

---

## 测试

```bash
pytest                                    # 全量
pytest tests/test_api.py -v               # API 测试
pytest tests/test_services.py -v          # 服务层测试
pytest --cov=app --cov-report=term-missing
```

---

## 文档索引

| 文档 | 内容 |
|---|---|
| `CLAUDE.md` | 项目指南：架构、入口指引、已知缺口、订阅号约束 |
| `deploy.md` | 详细部署指南（Docker / SSL / 常见问题） |
| `docs/DATA_FLOW.md` · `docs/FLOW.md` · `docs/TECHNICAL_FLOW.md` | 数据流与流程 |
| `docs/PLATFORM_INTEGRATION.md` | 多平台接入规范 |
| `docs/OPERATIONS.md` · `docs/OPERATIONS_GUIDE.md` | 运维手册 |
| `docs/OPTIMIZATION_ANALYSIS.md` · `docs/PROJECT_SUMMARY.md` | 优化记录与项目总览 |

---

## 订阅号约束（区别于服务号）

公众号后台「账号类型」是**订阅号**，有几条硬性限制，改动相关代码时必须注意：

1. **没有模板消息**：订阅号无 `template/send` 权限（微信返回 `errcode=43004`）。项目内已完全删除模板消息代码，所有主动通知走 `send_custom_message` 客服消息。
2. **每日群发配额**：`freepublish/submit` 个人订阅号 1 篇/天，认证订阅号 / 服务号 4 篇/天（`WECHAT_DAILY_PUBLISH_QUOTA`）。配额耗尽时多余文章自动降级为「只存草稿」，不调微信 API。
3. **客服消息 48h 窗口**：`send_custom_message` 只能发给 48h 内互动过的用户，否则 `errcode=45015`。`app/api/v1/webhook.py` 在关注 / 点击 / 文本消息时刷新 `last_interact_time`。
4. **草稿不限量**：`draft/add` 不受配额限制。默认流程是「先存草稿 → 立即群发」，配额耗尽则退化为只存草稿、由人工在后台群发。
5. **网页授权**：订阅号只能 `snsapi_userinfo`（弹出授权），本项目目前未使用网页授权。

---

## License

[MIT](LICENSE)
