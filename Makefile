# Makefile for wechat-ai-publisher

.PHONY: help build up down restart ps logs logs-app logs-worker logs-consumer test test-docker

# ============ Build ============
build:                          ## 重新构建所有镜像（不启动）
	docker compose build --pull

build-app:                     ## 仅重新构建 app 镜像
	docker compose build app

build-consumer:                ## 仅重新构建 consumer 镜像
	docker compose build consumer-generate consumer-publish consumer-push consumer-toutiao-publish

build-web:                     ## 重新构建前端并重启 nginx
	docker compose stop build-web
	docker compose run --rm build-web npm run build
	docker compose up -d nginx

# ============ Start / Stop ============
up:                             ## 启动所有容器
	docker compose up -d

rebuild:                        ## 重建所有镜像并启动容器
	docker compose build --pull
	docker compose up -d --remove-orphans

restart:                        ## 重启所有容器
	docker compose restart

restart-app:                    ## 重启 app + celery-worker
	docker compose restart app celery-worker

restart-consumer:               ## 重启所有消费者
	docker compose restart consumer-generate consumer-publish consumer-push consumer-toutiao-publish

# ============ Container Ops ============
stop:                           ## 暂停所有容器
	docker compose stop

stop-app:                       ## 暂停 app + celery-worker
	docker compose stop app celery-worker

rm:                            ## 删除所有已停止的容器
	docker compose rm

rm-all:                         ## 强制删除所有容器（运行中也会删）
	docker compose rm -f

# ============ Logs ============
logs:                           ## 查看所有容器日志
	docker compose logs -f

logs-app:                      ## 查看 app 日志
	docker compose logs -f app

logs-worker:                    ## 查看 celery-worker 日志
	docker compose logs -f celery-worker

logs-consumer:                  ## 查看所有消费者日志
	docker compose logs -f consumer-generate consumer-publish consumer-push consumer-toutiao-publish

logs-consumer-generate:         ## 查看 generate-consumer 日志
	docker compose logs -f consumer-generate

logs-consumer-publish:          ## 查看 publish-consumer 日志
	docker compose logs -f consumer-publish

logs-consumer-toutiao:          ## 查看 toutiao-consumer 日志
	docker compose logs -f consumer-toutiao-publish

# ============ Test ============
test:                          ## 本地跑 pytest（需先 pip install -r requirements.txt）
	python -m pytest -q

test-cov:                      ## 本地跑 pytest 并出覆盖率报告
	python -m pytest --cov=app --cov-report=term-missing

test-docker:                   ## 在容器内跑 pytest（镜像已带 tests/）
	docker compose run --rm app python -m pytest -q

# ============ Status ============
ps:                             ## 查看容器状态
	docker compose ps

# ============ Help ============
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
