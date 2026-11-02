# 运维手册 / Operations Handbook

> 项目：`wechat-ai-publisher`（公众号 AI 自动发布）
> 栈：FastAPI + PostgreSQL + Redis + Kafka + Celery
> 部署方式：docker-compose（首选），本机 `uvicorn`（开发）

---

## 0. 速查表 (TL;DR)

```bash
# 看所有容器
docker compose ps

# 看某个容器日志
docker logs wechat-app --tail=100 -f

# 进容器 (交互 shell)
docker exec -it wechat-postgres sh      # alpine 基础镜像只有 sh，没有 bash
docker exec -it wechat-app sh           # 业务镜像用 sh

# 关键端口
# 8888 → API + Swagger
# 5555 → PostgreSQL (容器内即监听 5555, 主机映射 5555:5555)
# 6666 → Redis (容器内即监听 6666, 主机映射 6666:6666)
# 9090 → Kafka UI
# 9092/9093 → Kafka broker
```

---

## 1. 容器总览

docker compose 里所有服务的 `container_name` 一览（命令以 `docker` 或 `docker compose` 开头都可以）：

| 容器名 | 镜像 | 角色 | 主机端口 |
|---|---|---|---|
| `wechat-postgres` | postgres:15-alpine | 主数据库 | `5555→5555`（容器内 PG 监听 5555，`docker/postgres/postgresql.conf`） |
| `wechat-redis` | redis:7.2-alpine | 缓存 / Celery broker | `6666→6666`（容器内 Redis 监听 6666） |
| `wechat-zookeeper` | confluentinc/cp-zookeeper:7.4.4 | Kafka 协调 | — |
| `wechat-kafka` | confluentinc/cp-kafka:7.2.1 | 消息队列 | `9092,9093` |
| `wechat-kafka-ui` | provectuslabs/kafka-ui:v0.7.1 | Kafka Web UI | `9090→8080` |
| `wechat-app` | 自建 (Dockerfile) | FastAPI 主服务 | `8888→8888` |
| `wechat-celery-worker` | 自建 | Celery worker (4 队列) | — |
| `wechat-celery-beat` | 自建 | Celery beat 调度器 | — |
| `wechat-consumer-generate` | 自建 | Kafka → 文章生成 | — |
| `wechat-consumer-publish` | 自建 | Kafka → 文章发布 | — |
| `wechat-consumer-push` | 自建 | Kafka → 客服消息 | — |
| `wechat-migrate` | 自建 | Alembic 一次性迁移 | — |
| `wechat-nginx` | nginx:1.25-alpine | 反代 (profile=with-nginx) | — |

**服务依赖关系（启动顺序）**：

```
postgres / redis / zookeeper (基础)
    └── kafka
        └── migrate (一次性, 跑完退出)
            ├── celery-beat
            ├── celery-worker
            ├── consumer-generate / consumer-publish / consumer-push
            └── app
                └── nginx (可选)
```

---

## 2. 查看容器

### 2.1 状态列表

```bash
# 简洁（只显示本 compose 内的服务）
docker compose ps

# 全量（看到容器 ID / 镜像 / 端口）
docker ps
docker ps -a            # 含已退出的 (migrate / crashed worker)
```

输出字段解读：

| 字段 | 含义 | 常见问题值 |
|---|---|---|
| `STATUS` | 容器状态 | `Up X minutes (healthy)` 正常；`Restarting (N)` pid 冲突；`Exited (1)` 启动失败 |
| `PORTS` | 端口映射 | `0.0.0.0:8888->8888/tcp` 表示主机可访问 8888 |
| `(unhealthy)` | healthcheck 不通过 | 业务刚启动 / 依赖未就绪 |

### 2.2 资源占用

```bash
docker stats --no-stream                    # 所有容器实时资源
docker stats --no-stream wechat-app         # 单个
```

### 2.3 启停

```bash
docker compose up -d                        # 启动 (后台)
docker compose up -d postgres redis kafka   # 启指定服务
docker compose stop celery-worker           # 停（保留容器）
docker compose restart celery-worker        # 重启
docker compose restart celery-beat          # beat 卡死时
docker compose down                         # 全部停+删容器（保留卷）
docker compose down -v                      # 连数据卷一起删（慎用！删库）
```

---

## 3. 各容器调试工具

### 3.1 PostgreSQL (`wechat-postgres`)

```bash
# 一次性查询 (推荐, 不进交互)
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT id, title FROM hot_topics ORDER BY id DESC LIMIT 5;"

# 进入 psql 交互终端
docker exec -it -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher
```

> 容器内默认 `localhost` 实际指向容器自己的回环。`-h localhost` 不是连接 docker host，而是容器内的 PG server。

**速查 SQL**：

```sql
-- 今日话题
SELECT id, title, heat_score, status, source_url, metadata
FROM hot_topics
WHERE create_time >= CURRENT_DATE
ORDER BY heat_score DESC;

-- 文章
SELECT id, title, status, wechat_media_id, wechat_publish_id,
       error_msg, publish_time
FROM generated_articles
ORDER BY id DESC LIMIT 10;

-- 用户选择 (生成文章用)
SELECT choice_id, topic_id, user_openid, is_processed, choose_time
FROM user_choices
ORDER BY choose_time DESC LIMIT 20;

-- 今天已发布多少篇 (对配额)
SELECT count(*) FROM generated_articles
WHERE status = 'PUBLISHED' AND publish_time >= CURRENT_DATE;
```

### 3.2 Redis (`wechat-redis`)

```bash
# 一次性查
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning \
    PING

# 进入交互 (注意 -a 是明文, 生产用 ACL 更安全)
docker exec -it wechat-redis \
    redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning

# 切数据库 (业务缓存用 0, Celery broker 用 1, Celery result backend 用 2)
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw -n 0 PING
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw -n 1 PING
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw -n 2 PING
```

**常用 key**：

| key 模式 | 含义 |
|---|---|
| `wechat:access_token:{appid}` | 微信 access_token 缓存（按公众号隔离） |
| `wechat:lock:publish_article:<id>` | publish 分布式锁（TTL 默认 600s） |
| `platform:lock:publish:<article_id>:<account_id>` | 多平台发布互斥锁（TTL 120s） |
| `celery-task-meta-*` | Celery 任务结果 (db=2) |

### 3.3 Kafka (`wechat-kafka` + `wechat-kafka-ui`)

**首选 Web UI**：浏览器打开 `http://localhost:9090`，可看 topic / consumer group / offset。

**CLI（kafka-console-consumer）**：

```bash
# 容器内所有命令都在 wechat-kafka 里执行
docker exec -it wechat-kafka bash

# 列 topic
kafka-topics --bootstrap-server kafka:9093 --list

# 看 generate-articles topic 的最新消息
kafka-console-consumer --bootstrap-server kafka:9093 \
    --topic generate-articles --from-beginning --max-messages 5 \
    --property print.key=true

# 看 consumer group lag
kafka-consumer-groups --bootstrap-server kafka:9093 \
    --describe --group wechat-consumer-group-generate
```

> **坑**：容器内 `bootstrap-server` 用 `kafka:9093`（容器间通信），主机用 `localhost:9092`（外部监听）。两者别混。

**5 个 topic** (`.env` 可改，含 DLQ)：

| topic | 用途 |
|---|---|
| `crawl-topics` | 爬取完成 → 通知生成 |
| `generate-articles` | 用户选话题 → LLM 生成 |
| `push-notifications` | 客服消息推送 |
| `publish-articles` | 发布事件 |
| `wechat-dlq` | 死信队列（超过 `kafka_consumer_max_attempts` 后进这里） |

### 3.4 FastAPI (`wechat-app`)

```bash
# Swagger 文档
open http://localhost:8888/docs

# 健康检查
curl -s http://localhost:8888/health

# 触发爬取 (需要 admin token)
curl -X POST http://localhost:8888/api/v1/topics/crawl \
     -H "X-Admin-Token: $ADMIN_API_KEY"
```

进容器调试：

```bash
docker exec -it wechat-app sh

# 看代码 (本地源码已 volume 挂到 /app/app)
ls /app/app
cat /app/app/services/crawler.py | head

# 一次性跑 Python (比如手动调服务层)
docker exec wechat-app python -c "
from app.core.database import SessionLocal
from app.services.llm_provider import LLMProviderService
from app.services.llm_clients import build_llm_client
db = SessionLocal()
svc = LLMProviderService(db)
p = svc.get_default_provider()
key = svc.decrypt_api_key(p.id)
c = build_llm_client(p.provider_type, key, p.api_url, p.model, p.temperature, p.timeout)
print(c.generate_brief('测试话题'))
"
```

### 3.5 Celery (`wechat-celery-worker` / `wechat-celery-beat`)

**主动触发一个任务（不依赖 beat 调度）**：

```bash
# 触发爬虫
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.crawl_tasks.crawl_hot_topics

# 触发每日 digest
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.daily_publish_digest

# 发布指定文章
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.publish_article -a "[23]"

# 推送给所有用户
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.push_tasks.push_topics_to_users
```

**队列（按用途拆分）**：

```
crawl    → crawl_hot_topics / crawl_for_account
article  → generate_article / publish_article / daily_publish_digest / process_user_choices / generate_deep_article
push     → push_topics_to_users / push_for_account
default  → 未使用（保留）
```

队列由 `app/tasks/celery_app.py:165-169` 的 `task_routes` 按模块前缀路由：`app.tasks.crawl_tasks.*` → `crawl`，`app.tasks.article_tasks.*` → `article`，`app.tasks.push_tasks.*` → `push`。

worker 启动命令（`docker compose.yml`）：

```bash
celery -A app.tasks.celery_app worker --loglevel=info \
  --concurrency=4 --pool=threads \
  --queues=crawl,article,push,default
```

**beat 常见故障**：`Restarting (73) ... ERROR: Pidfile already exists` —— 是历史 pidfile 没清理。

```bash
docker compose down
docker volume rm wechat-ai-publisher_celerybeat_data
docker compose up -d
```

### 3.6 Kafka 消费者（`wechat-consumer-*`）

3 个独立长进程，各自订阅一个 topic：

| 容器 | topic |
|---|---|
| `wechat-consumer-generate` | `generate-articles` |
| `wechat-consumer-publish` | `publish-articles` |
| `wechat-consumer-push` | `push-notifications` |

```bash
# 看哪个 consumer 挂了
docker logs wechat-consumer-generate --tail=50
```

---

## 4. 数据验证清单

> 每改一处代码，建议跑一遍下面 5 步确认链路通了。

### 4.1 爬取 → DB

```bash
# 1. 触发
curl -X POST http://localhost:8888/api/v1/topics/crawl \
     -H "X-Admin-Token: $ADMIN_API_KEY"

# 2. 看 celery 日志
docker logs wechat-celery-worker --tail=20 | grep "爬取\|保存\|新闻原文"

# 3. 查 DB
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT count(*) FROM hot_topics WHERE create_time >= CURRENT_DATE;"
```

### 4.2 文章生成

```bash
# 1. 触发 digest (会调默认 LLM 给每条话题生成简讯)
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.daily_publish_digest

# 2. 看 worker 是否调了 LLM
docker logs wechat-celery-worker --tail=20 | grep "简讯\|digest"

# 3. 看新文章
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -t -c "SELECT title, status FROM generated_articles ORDER BY id DESC LIMIT 1;"
```

### 4.3 发布到微信

```bash
# 1. 手动 publish 一篇
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.publish_article -a "[<article_id>]"

# 2. 看草稿/发布结果
docker logs wechat-celery-worker --tail=20 | grep "草稿\|发布"

# 3. 看文章状态 (PUBLISHED 才有 publish_id)
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT id, status, wechat_media_id, wechat_publish_id, error_msg \
        FROM generated_articles ORDER BY id DESC LIMIT 1;"
```

错误码对照：

| errcode | 含义 | 处理 |
|---|---|---|
| 0 | 成功 | — |
| 40001 | access_token 失效 | worker 已自动刷新重试 |
| 43004 | 需要模板消息（订阅号无） | 已废弃，不应再出现（项目只用客服消息 `send_custom_message`） |
| 45009 / 45004 | 每日配额耗尽 | 已落 DRAFT，等明天 |
| 48001 | api unauthorized | 当前账号没权限；检查订阅号认证状态 |
| 45015 | 用户不在客服消息 48h 窗口 | 正常路径已在发送前预检，出现说明 `last_interact_time` 过期 |

> 订阅号**没有模板消息权限**（`template/send` 不可用），本项目所有主动通知统一走客服消息 `send_custom_message`，并受 **48h 互动窗口**限制（用户须在 48h 内关注 / 发消息 / 点菜单）。

### 4.4 用户推送（客服消息）

```bash
# 推送
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.push_tasks.push_topics_to_users

# 看 48h 窗口过滤掉了多少
docker logs wechat-celery-worker --tail=20 | grep "48h\|window\|skipped"
```

---

## 5. 日志速查 (grep cheat sheet)

```bash
# 抓取链路
docker logs wechat-celery-worker 2>&1 | grep "爬取\|新闻原文\|保存"

# 文章链路
docker logs wechat-celery-worker 2>&1 | grep "digest\|草稿\|发布\|简讯"

# 推送链路
docker logs wechat-consumer-push --tail=100 | grep "推送\|errcode"

# 微信 API 错误
docker logs wechat-celery-worker 2>&1 | grep "errcode"

# Kafka 消费
docker logs wechat-consumer-generate --tail=100 | grep "received\|处理"
```

---

## 6. 常见故障排除

### 6.1 `celery-beat` 一直 `Restarting (N)`

Pidfile 残留。修：

```bash
docker compose down
docker volume rm wechat-ai-publisher_celerybeat_data
docker compose up -d celery-beat
```

### 6.2 `celery-worker` unhealthy

`docker ps` 显示 `Restarting` 或 `(unhealthy)`：

```bash
docker logs wechat-celery-worker --tail=50 | tail -30
# 多数情况: 
#   1) redis 连不上 → 检查 wechat-redis 状态
#   2) kafka 连不上 → 检查 wechat-kafka healthcheck
#   3) 业务代码 bug → 修后 docker compose build celery-worker + up -d
```

### 6.3 改代码后不生效

代码通过 volume 挂载 (`./app:/app/app`)，改了 `.py` **应该立即生效**；但 Celery 进程在启动时 import 模块，运行时不重新加载。

```bash
docker compose restart celery-worker
docker compose restart celery-beat
docker compose restart consumer-generate consumer-publish consumer-push
```

`wechat-app` 是 uvicorn, 不挂 volume 的部分要 rebuild：

```bash
docker compose build app celery-worker celery-beat \
                   consumer-generate consumer-publish consumer-push
docker compose up -d
```

### 6.4 数据库连不上

```bash
docker ps wechat-postgres        # 确认 Up
docker logs wechat-postgres --tail=20
docker exec wechat-postgres pg_isready -p 5555 -U wechat_user
```

### 6.5 微信公众号 API 报 `errcode=48001`

订阅号没群发 / 客服权限。两条路：

1. 升级订阅号 / 换服务号
2. 看 `.env` 的 `WECHAT_DAILY_PUBLISH_QUOTA` 是否被改大过头

### 6.6 LLM 简讯全是空

`docker logs wechat-celery-worker | grep "digest 简讯"`：
- 401 → `llm_providers` 表里的 API Key 解密后是否正确
- 网络 → `llm_providers.api_url` 是否可达

### 6.7 爬虫被百度 captcha 拦了

`docker logs wechat-celery-worker | grep "captcha\|captcha\|wappass"` 出现 → 短时间内请求太多。

退路：`_resolve_original_news_url` 失败时会回退到 `&sa=fyb_news` 聚合页 URL，不会崩。

---

## 7. 数据备份 / 还原

### 7.1 备份 PostgreSQL

```bash
docker exec wechat-postgres pg_dump -p 5555 -U wechat_user \
    -d wechat_ai_publisher > backup_$(date +%Y%m%d).sql
```

### 7.2 还原

```bash
cat backup_20260709.sql | docker exec -i wechat-postgres psql \
    -p 5555 -U wechat_user -d wechat_ai_publisher
```

### 7.3 Redis 备份（按需）

```bash
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning \
    SAVE           # 触发一次 RDB
docker cp wechat-redis:/data/dump.rdb ./redis_$(date +%Y%m%d).rdb
```

> Redis 只是缓存 + Celery broker，丢了不会丢业务数据，重启会重建。

---

## 8. 关键运维命令汇总

```bash
# === 全量健康巡检 ===
docker compose ps                           # 服务状态
docker stats --no-stream                    # 资源
docker logs --tail=50 wechat-app            # 最近应用日志
docker logs --tail=50 wechat-celery-worker  # 最近 worker 日志

# === 数据巡检 ===
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT status, count(*) FROM generated_articles \
        WHERE create_time >= CURRENT_DATE GROUP BY status;"

# === 重启单个服务（保留数据）===
docker compose restart <service_name>

# === 重建镜像（改了 Dockerfile / requirements.txt）===
docker compose build app celery-worker celery-beat \
                   consumer-generate consumer-publish consumer-push
docker compose up -d

# === 全清重启（保留卷）===
docker compose down && docker compose up -d

# === 全清 + 删库（开发环境慎用！）===
docker compose down -v && docker compose up -d
```

---

## 9. 联系 / 排查流程建议

1. **先看应用日志**（`wechat-app` / `wechat-celery-worker`），不是直接看 DB。
2. **找最近一次 error**：`docker logs X 2>&1 | grep -i "error\|exception" | tail`。
3. **复现 → 验证**：用 `celery call` 主动跑一次任务，看输出。
4. **再查 DB**：跑完看 `error_msg` 字段、状态、`publish_time`。
5. **再走外部依赖**：WeChat API / LLM 供应商 / 百度。

---

最后更新：本手册由仓库运维实践整理，跟随 `docker-compose.yml` 调整。