# 运维手册

> 项目：`wechat-ai-publisher`（公众号 AI 自动发布）
> 栈：FastAPI + PostgreSQL + Redis + Kafka + Celery + LLM（多供应商可配置）
> 部署方式：docker-compose（首选），本地 `uvicorn`（开发调试）

---

## 一、快速启动

### 1.1 首次部署

```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env，填入必填项（按文件内注释）
# 必填：SECRET_KEY / PG_PASSWORD / REDIS_PASSWORD / DEEPSEEK_API_KEY
# 必填：WECHAT_APP_ID / WECHAT_APP_SECRET / WECHAT_TOKEN

# 3. 一键启动所有服务
bash scripts/deploy.sh
```

### 1.2 启动全部服务

```bash
docker compose up -d
```

### 1.3 启动（含 Nginx 反代）

```bash
docker compose --profile with-nginx up -d
```

### 1.4 查看服务状态

```bash
docker compose ps
```

### 1.5 访问入口

| 服务 | 地址 |
|---|---|
| API | http://localhost:8888 |
| Swagger 文档 | http://localhost:8888/docs |
| Kafka Web UI | http://localhost:9090 |
| 管理后台（需开启 Nginx） | http://localhost |

---

## 二、日志查看

### 2.1 实时查看所有日志

```bash
docker compose logs -f
```

### 2.2 按服务查看

```bash
# API 服务
docker compose logs -f app

# Celery Worker
docker compose logs -f celery-worker

# Celery Beat 调度器
docker compose logs -f celery-beat

# Kafka 生成消费者
docker compose logs -f consumer-generate

# Kafka 推送消费者
docker compose logs -f consumer-push
```

### 2.3 最近 N 行日志

```bash
docker compose logs --tail=100 app
```

### 2.4 按关键字过滤

```bash
# 爬虫链路
docker compose logs celery-worker 2>&1 | grep "爬取\|保存\|新闻原文"

# 文章生成链路
docker compose logs celery-worker 2>&1 | grep "digest\|草稿\|发布\|简讯"

# 推送链路
docker compose logs consumer-push --tail=100 | grep "推送\|errcode"

# 微信 API 错误
docker compose logs celery-worker 2>&1 | grep "errcode"

# Kafka 消费
docker compose logs consumer-generate --tail=100 | grep "received\|处理"
```

### 2.5 日志聚合查询

```bash
# 应用报错
docker compose logs app 2>&1 | grep -i "error\|exception"

# Worker 报错
docker compose logs celery-worker 2>&1 | grep -i "error\|exception"
```

---

## 三、镜像构建

### 3.1 构建所有自定义镜像

```bash
docker compose build
```

### 3.2 构建指定服务镜像

```bash
docker compose build app
docker compose build celery-worker celery-beat
docker compose build consumer-generate consumer-push
```

### 3.3 不使用缓存强制重建

```bash
docker compose build --no-cache app
```

### 3.4 构建后重启服务

```bash
docker compose build app celery-worker celery-beat consumer-generate consumer-push
docker compose up -d
```

### 3.5 查看镜像构建历史

```bash
docker history wechat-app:latest
```

---

## 四、服务启停

### 4.1 启动/停止/重启

```bash
docker compose up -d                    # 启动（后台）
docker compose stop celery-worker       # 停止（保留容器）
docker compose restart celery-worker    # 重启
docker compose restart celery-beat     # beat 卡死时重启
docker compose down                    # 全部停止+删容器（保留数据卷）
```

### 4.2 重启单个服务（代码修改后生效）

```bash
# API（uvicorn，代码通过 volume 挂载，改 .py 立即生效）
docker compose restart app

# Celery（需重启才能加载新代码）
docker compose restart celery-worker
docker compose restart celery-beat

# Kafka 消费者（需重启）
docker compose restart consumer-generate consumer-push
```

### 4.3 完全重置（慎用，会删库）

```bash
# 停止并删除所有容器和卷（数据全部丢失）
docker compose down -v

# 重新启动
docker compose up -d
```

---

## 五、进入容器调试

```bash
# API 容器（Alpine 用 sh）
docker exec -it wechat-app sh

# PostgreSQL 容器
docker exec -it wechat-postgres sh

# Celery Worker 容器
docker exec -it wechat-celery-worker sh

# Kafka 容器（可用 kafka-topics 等 CLI）
docker exec -it wechat-kafka bash
```

---

## 六、数据库操作

### 6.1 进入 psql 交互终端

```bash
docker exec -it -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher
```

### 6.2 一次性 SQL 查询（不进交互终端）

```bash
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT id, title, status FROM hot_topics ORDER BY id DESC LIMIT 5;"
```

### 6.3 常用查询

```sql
-- 今日话题
SELECT id, title, heat_score, status
FROM hot_topics
WHERE create_time >= CURRENT_DATE
ORDER BY heat_score DESC;

-- 文章列表
SELECT id, title, status, wechat_media_id, error_msg
FROM generated_articles
ORDER BY id DESC LIMIT 10;

-- 今日发布量（对照配额）
SELECT count(*) FROM generated_articles
WHERE status = 'PUBLISHED' AND publish_time >= CURRENT_DATE;
```

### 6.4 数据库备份

```bash
docker exec wechat-postgres pg_dump -p 5555 -U wechat_user \
    -d wechat_ai_publisher > backup_$(date +%Y%m%d).sql
```

### 6.5 数据库还原

```bash
cat backup_20241008.sql | docker exec -i wechat-postgres psql \
    -p 5555 -U wechat_user -d wechat_ai_publisher
```

---

## 七、Redis 操作

### 7.1 进入 redis-cli

```bash
docker exec -it wechat-redis \
    redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning
```

### 7.2 常用命令

```bash
# 测试连接
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning PING

# 查看 access_token 缓存
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning \
    GET wechat:access_token:<appid>

# 查看 key 总数
docker exec wechat-redis redis-cli -p 6666 -a wechat_redis_pw --no-auth-warning DBSIZE
```

---

## 八、Kafka 操作

### 8.1 Web UI（推荐）

浏览器访问：http://localhost:9090

可查看 topic 列表、消息内容、consumer group offset。

### 8.2 进入 Kafka 容器使用 CLI

```bash
docker exec -it wechat-kafka bash
```

### 8.3 查看 topic 列表

```bash
kafka-topics --bootstrap-server kafka:9093 --list
```

### 8.4 查看消费者组 lag

```bash
kafka-consumer-groups --bootstrap-server kafka:9093 \
    --describe --group wechat-consumer-group-generate
```

### 8.5 消费指定 topic 最新消息

```bash
kafka-console-consumer --bootstrap-server kafka:9093 \
    --topic generate-articles --from-beginning --max-messages 5 \
    --property print.key=true
```

---

## 九、Celery 任务操作

### 9.1 主动触发任务（不依赖 beat 调度）

```bash
# 触发爬虫
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.crawl_tasks.crawl_hot_topics

# 触发每日 digest
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.daily_publish_digest

# 发布指定文章
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.article_tasks.publish_article -a "[<article_id>]"

# 推送话题给用户
docker exec wechat-celery-worker celery -A app.tasks.celery_app \
    call app.tasks.push_tasks.push_topics_to_users
```

> 订阅号**没有模板消息权限**（`template/send` 不可用），主动通知统一走客服消息 `send_custom_message`，受 **48h 互动窗口**限制（用户须在 48h 内关注 / 发消息 / 点菜单）。`push_topics_to_users` 会先用 SQL 预过滤 `last_interact_time >= now-48h`。

### 9.2 查看待执行任务

```bash
docker exec wechat-celery-worker celery -A app.tasks.celery_app inspect scheduled
```

### 9.3 查看活跃任务

```bash
docker exec wechat-celery-worker celery -A app.tasks.celery_app inspect active
```

---

## 十、常见故障排除

### 10.1 celery-beat 一直 Restarting

Pidfile 残留：

```bash
docker compose down
docker volume rm wechat-ai-publisher_celerybeat_data
docker compose up -d
```

### 10.2 celery-worker unhealthy

```bash
docker logs celery-worker --tail=50
```

常见原因：
- Redis 连不上 → 检查 `wechat-redis` 状态
- Kafka 连不上 → 检查 `wechat-kafka` healthcheck
- 代码 bug → 修复后 `docker compose build celery-worker && docker compose up -d`

### 10.3 改代码后不生效

Celery 进程启动时 import 模块，运行时不重新加载：

```bash
docker compose restart celery-worker celery-beat consumer-generate consumer-push
```

### 10.4 微信公众号 API 报 `errcode=48001`

订阅号无权限，或 `.env` 中 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` 配置错误。

### 10.5 LLM 全部返回空

```bash
docker logs celery-worker | grep "LLM\|简讯\|generate"
```

- 401 → `llm_providers` 表里的 API Key 错误
- 网络问题 → 检查 `llm_providers.api_url` 是否可达

---

## 十一、健康巡检命令

```bash
# 服务状态
docker compose ps

# 资源占用
docker stats --no-stream

# 最近应用日志
docker compose logs --tail=50 app

# 最近 Worker 日志
docker compose logs --tail=50 celery-worker

# 今日文章发布统计
docker exec -e PGPASSWORD=wechat_user_pw wechat-postgres \
    psql -h localhost -p 5555 -U wechat_user -d wechat_ai_publisher \
    -c "SELECT status, count(*) FROM generated_articles \
        WHERE create_time >= CURRENT_DATE GROUP BY status;"
```
