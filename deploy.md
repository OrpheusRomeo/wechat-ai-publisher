# 部署指南

## 前置条件

1. Docker Desktop（推荐）或者 Docker Engine + Compose v2
2. 可选：启用 profile `with-nginx` 时才需要 SSL 证书，把证书放到 `./ssl/`
3. 环境变量：准备好合法的微信 AppID/Secret、DeepSeek API Key

## 一键部署（推荐）

```bash
# 进入项目目录（PowerShell）
cd D:\uwaysoft\personal_test\wechat-ai-publisher

# 1. 复制环境变量模板
cp .env.example .env
```

编辑 `.env`，必须修改以下几项：

| 变量 | 说明 |
|---|---|
| `SECRET_KEY` | 改成随机字符串（`openssl rand -hex 32`） |
| `ADMIN_API_KEY` | 管理后台 API token，**至少 16 个字符**（`openssl rand -hex 32`） |
| `PG_PASSWORD` | 数据库密码 |
| `REDIS_PASSWORD` | Redis 密码 |
| `DEEPSEEK_API_KEY` | 从 https://platform.deepseek.com 申请 |
| `WECHAT_APP_ID` / `WECHAT_APP_SECRET` / `WECHAT_TOKEN` | 微信公众平台后台 |

```bash
# 2. 一键启动
bash scripts/deploy.sh
# 等价于: docker compose up -d

# 2'. 需要管理后台（Vue 3 前端 + Nginx）时
bash scripts/deploy.sh --web
# 等价于: docker compose --profile with-nginx up -d
```

启动后脚本会输出访问入口：

| 入口 | 地址 |
|---|---|
| API | http://localhost:8888 |
| API 文档（Swagger） | http://localhost:8888/docs |
| ReDoc | http://localhost:8888/redoc |
| Kafka UI | http://localhost:9090 |
| 管理后台 | http://localhost （需 `--web` / `with-nginx` profile） |

管理后台默认账号：用户名为 `admin`，密码取自 `.env` 的 `ADMIN_DEFAULT_PASSWORD`
（未设置时为 `admin123`，**仅限开发环境**）。首次登录后请立即修改密码。

## 包含 Nginx 反向代理

```bash
docker compose --profile with-nginx up -d
```

启动前把 SSL 证书放到 `./ssl/` 目录（`./ssl/fullchain.pem`、`./ssl/privkey.pem`），Nginx 会自动挂载。
`wechat-build-web` 服务负责构建前端并把产物输出到 `web_dist` volume，Nginx 挂载该 volume 作为站点根目录。

## 部署流程内部发生了什么

### 数据库迁移

schema 走 Alembic 迁移，`wechat-migrate` 是一次性服务：

```bash
alembic upgrade heads
```

compose 中 `migrate` 服务 `restart: "no"`，其余业务容器 `depends_on: service_completed_successfully`，所以迁移完成后才会启动应用。
`init_db()` 已废弃（仅打 warning，不建表），不要再依赖它建表。

### 应用容器启动顺序

执行顺序（`scripts/entrypoint.sh app`）：

```bash
python -c "from app.core.database import init_db; init_db()"              # 1. 已废弃，仅告警
python -c "from app.core.kafka_client import init_topics; init_topics()"  # 2. 预创建 4 个 Kafka topic
uvicorn app.main:app --host 0.0.0.0 --port 8888 --workers ${WORKER_CONCURRENCY:-4}
```

app 容器启动前会等 postgres/redis/kafka 的 healthcheck 通过，celery-worker、consumer-generate、consumer-push 又会等 kafka/postgres 健康后才启动，所以启动顺序是对的。

## docker compose 服务清单

```bash
docker compose ps
```

| 服务 | 容器名 | 端口 | 说明 |
|---|---|---|---|
| postgres | wechat-postgres | 5555 | |
| redis | wechat-redis | 6666 | |
| zookeeper | wechat-zookeeper | - | Kafka 依赖 |
| kafka | wechat-kafka | 9092/9093 | |
| kafka-ui | wechat-kafka-ui | 9090 | |
| migrate | wechat-migrate | - | 一次性服务，执行 `alembic upgrade heads` |
| app (FastAPI) | wechat-app | 8888 | |
| celery-worker | wechat-celery-worker | - | |
| celery-beat | wechat-celery-beat | - | |
| consumer-generate | wechat-consumer-generate | - | |
| consumer-push | wechat-consumer-push | - | |
| consumer-publish | wechat-consumer-publish | - | 消费 publish-articles，多平台发布 |
| consumer-toutiao-publish | wechat-consumer-toutiao-publish | - | 头条平台发布 |
| build-web | wechat-build-web | - | `with-nginx` profile，构建前端 |
| nginx (可选) | wechat-nginx | 80/443 | `with-nginx` profile |

## 常用运维命令

```bash
# 查看所有服务状态
docker compose ps

# 实时查看某个服务日志
docker compose logs -f app
docker compose logs -f celery-worker
docker compose logs -f consumer-generate

# 重启单个服务
docker compose restart celery-worker

# 进入容器调试
docker compose exec app bash
docker compose exec postgres psql -U wechat_user -d wechat_ai_publisher

# 触发一次爬取（在 app 容器内）
docker compose exec app python -c "
from app.tasks.crawl_tasks import crawl_hot_topics
print(crawl_hot_topics.delay().id)
"

# 备份数据库
bash scripts/backup.sh     # 生成 backups/postgres_<时间戳>.sql.gz

# 停止所有服务（保留数据卷）
docker compose down

# 停止并清空数据卷（重置）
docker compose down -v
```

## 调度任务一览

已配置的 Celery Beat 定时任务（`app/tasks/celery_app.py`）：

| 时间 | 任务 | 队列 |
|---|---|---|
| 每天 08:00 | `crawl_hot_topics` — 爬取百度热搜 | crawl |
| 每天 09:00 | `push_topics_to_users` — 客服消息推送（已弃用，保留供认证号复用） | push |
| 每天 09:30 | `daily_publish_digest` — 群发一篇今日话题日报 | article |
| 每 30 分钟 | `process_user_choices` — 处理用户选择，进入生成流程 | article |

另外每个平台账号可单独配置 `crawl_cron` / `push_cron`，Celery beat 启动时会从 `platform_accounts` 表动态注入 `crawl-{account_id}` / `push-{account_id}` 任务。

时区是 `Asia/Shanghai`（.env 的 `TIMEZONE`）。`CELERY_TIMEZONE` 已合并到 `TIMEZONE`，两者保持一致。

## 健康检查

```bash
# 简单探活
curl http://localhost:8888/health
# 返回: {"status":"healthy","timestamp":"..."}

# 列出所有话题
curl http://localhost:8888/api/v1/topics/

# 手动触发爬取
curl -X POST http://localhost:8888/api/v1/topics/crawl
# {"code":0,"message":"爬取任务已启动","data":{"task_id":"..."}}
```

## 部署后的可选验证清单

- [ ] 打开 http://localhost:9090 看到 Kafka 4 个 topic（crawl-topics / generate-articles / push-notifications / publish-articles）
- [ ] 打开 http://localhost:8888/docs 看到完整接口列表
- [ ] 调用 POST /api/v1/topics/crawl 后在 celery-worker 日志看到 "保存 N 条新话题"
- [ ] 配置好真实的 WECHAT_* 后能在 consumer-push 日志看到推送结果
- [ ] 登录 http://localhost 管理后台（admin / `$ADMIN_DEFAULT_PASSWORD`），确认平台账号与 LLM 配置可正常读写

## 如果你不用 Docker

WSL 的 miniconda 环境里也可以本地起：

```bash
wsl bash -ic "conda activate instant"

# 假设本机已起 Postgres / Redis / Kafka，地址写进 .env
cd /mnt/d/uwaysoft/personal_test/wechat-ai-publisher
pip install -r requirements.txt

# 起 FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload

# 起 Celery worker（新终端）
celery -A app.tasks.celery_app worker --loglevel=info \
  --queues=crawl,article,push,default --concurrency=4 --pool=threads

# 起 Celery beat（新终端）
celery -A app.tasks.celery_app beat --loglevel=info

# 起 Kafka 消费者（新终端）
python -m consumers.generate_consumer
python -m consumers.push_consumer
python -m consumers.publish_consumer
```

本地跑的时候记得先执行一次 `alembic upgrade head` 建表。

## 常见坑

1. **SECRET_KEY 必须改**：默认的 `change-this-to-secure-random-key-in-production` 是占位符，生产环境会被审计到。生成：`openssl rand -hex 32`。
2. **ADMIN_API_KEY 必须改且 ≥16 字符**：`app/core/config.py` 里是 `Field(..., min_length=16)`，缺失或过短会导致容器启动失败。生成：`openssl rand -hex 32`。
3. **先修 .env 再起**：如果未填 `DEEPSEEK_API_KEY` 就 `docker compose up`，API 容器会因 Pydantic `Field(...)` 校验直接退出，错误会写进 `docker compose logs app`。
4. **端口冲突**：默认 5555/6666/9092/9093/9090/8888，如果本机已有占用，改 .env 中的 `PG_PORT`/`REDIS_PORT`/`KAFKA_PORT`/... 即可，docker compose 会自动映射。
5. **时区**：所有时间字段都是 `Asia/Shanghai`；celery-beat 调度也按此时区走。改时区请改 `.env` 的 `TIMEZONE`（`CELERY_TIMEZONE` 已被合并）。
6. **数据持久化**：五个 volume（`postgres_data`、`redis_data`、`zookeeper_data`、`kafka_data`、`celerybeat_data`）都用 local driver，删除容器不会丢数据。`docker compose down -v` 才会清空。
7. **微信回调地址**：`https://你的域名/webhook/wechat`，生产环境要把它填到微信公众号后台；本地调试可以用 ngrok / frp 反代。
8. **订阅号没有模板消息**：主动通知走客服消息（48h 互动窗口），每天 09:30 群发一篇日报覆盖全部粉丝；群发受每日配额限制（`WECHAT_DAILY_PUBLISH_QUOTA`），超额的文章会退化为草稿。
