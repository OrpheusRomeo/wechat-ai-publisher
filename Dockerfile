# syntax=docker/dockerfile:1
# 国内加速版: apt 走阿里云, pip 走阿里云; base image 保持默认
# python:3.11-slim 由 Docker daemon 镜像加速器 (daemon.json 配的 13 个) 自动代理
# 注: registry.cn-hangzhou.aliyuncs.com 是阿里云 ACR 私有仓库, 需要认证, 不能当公共 mirror 用
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# pip 用阿里云镜像 (持久写入, 后续 pip install 也会自动用)
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com

WORKDIR /app

# apt 换阿里云源: python:3.11-slim 是 Debian 12 (bookworm), 用新格式
# /etc/apt/sources.list.d/debian.sources; 老 /etc/apt/sources.list 留空也一起改
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
        sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
        sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY consumers ./consumers
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
# tests 随镜像带走, 便于在容器内跑 pytest (make test-docker)
COPY tests ./tests

RUN chmod +x ./scripts/*.sh 2>/dev/null || true \
    && mkdir -p /app/logs /app/data /app/backups

EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --retries=5 \
    CMD curl -fsS http://localhost:8888/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888"]
