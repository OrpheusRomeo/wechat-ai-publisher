#!/bin/bash
# 构建 Vue 3 管理前端
# 使用方法: bash scripts/build_web.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/web"

echo "=== 安装前端依赖 ==="
npm install --registry=https://registry.npmmirror.com

echo "=== 构建生产版本 ==="
npm run build

echo "=== 构建完成 ==="
echo "输出目录: $ROOT/web/dist"
echo "部署方式: docker compose --profile with-nginx up -d"
