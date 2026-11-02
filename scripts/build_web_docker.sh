#!/usr/bin/env bash
# 重建前端 (Docker 路径), 注入构建时间戳和 git commit 信息到产物.
#
# 用法:
#   bash scripts/build_web_docker.sh
#
# 与 docker-compose build-web service 不同的是, 这个脚本显式把 BUILD_TIMESTAMP /
# GIT_COMMIT / GIT_BRANCH / GIT_MSG 通过 --build-arg 传入 (docker-compose 的 args
# 不支持 shell 命令替换, 之前用 $(date ...) 会被当成字面值).
#
# 改完前端代码后跑一次, nginx 会在下次浏览器访问时 serve 新产物.
set -euo pipefail
cd "$(dirname "$0")/.."

# 当前 UTC 时间, 注入到产物里显示在右下角
BUILD_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# git 信息, 非 git 环境 fallback 到 unknown (例如 export 出来的 tarball)
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "no-git")
GIT_MSG=$(git log -1 --pretty=%s 2>/dev/null || echo "")

echo "build_ts=$BUILD_TS git=$GIT_HASH branch=$GIT_BRANCH"

docker compose --profile with-nginx build \
  --build-arg BUILD_TIMESTAMP="$BUILD_TS" \
  --build-arg GIT_COMMIT="$GIT_HASH" \
  --build-arg GIT_BRANCH="$GIT_BRANCH" \
  --build-arg GIT_MSG="$GIT_MSG" \
  build-web

docker compose --profile with-nginx up -d build-web
echo "OK — 浏览器刷新即可看到右下角新 build 时间"