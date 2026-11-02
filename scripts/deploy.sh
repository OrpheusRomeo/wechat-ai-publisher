#!/bin/bash
# scripts/deploy.sh
# 一键部署脚本
#
# 基础模式: bash scripts/deploy.sh
#   → API (8888) + Celery + Kafka consumers + PostgreSQL + Redis
#
# 完整模式 (含管理前端 + Nginx):
#   bash scripts/deploy.sh --web
#   → 以上所有 + 自动 npm build + Nginx (80/443)

set -e

WITH_WEB=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --web) WITH_WEB=true; shift ;;
        *) shift ;;
    esac
done

echo "🚀 启动微信AI文章发布系统..."

# 检查环境变量
if [ ! -f .env ]; then
    echo "❌ .env 文件不存在，请从 .env.example 复制并配置"
    echo ""
    echo "    cp .env.example .env"
    echo "    # 编辑 .env 填入实际的密钥和配置"
    exit 1
fi

# 创建必要目录
mkdir -p logs data backups
mkdir -p docker/postgres docker/redis docker/nginx/conf.d

# 如果本地 web/node_modules 不存在 (非 Docker 模式开发)，可以先构建
PROFILE_ARGS=""
if $WITH_WEB; then
    PROFILE_ARGS="--profile with-nginx"
    echo ""
    echo "🔨 管理前端: docker compose build-web 服务将自动 npm install + build"
    echo "   如需本地开发前端，请运行:  cd web && npm install && npm run dev"
    echo ""
fi

# 启动所有服务
echo "📦 启动所有服务..."
docker compose $PROFILE_ARGS up -d

# 等待服务就绪
echo "⏳ 等待服务就绪..."
sleep 10

# 查看服务状态
echo ""
echo "📊 服务状态:"
docker compose ps

echo ""
echo "✅ 部署完成！"
echo ""
echo "📝 访问地址:"
echo "  - API:        http://localhost:8888"
echo "  - API 文档:   http://localhost:8888/docs"
echo "  - Kafka UI:   http://localhost:9090"
if $WITH_WEB; then
    echo "  - 管理后台:   http://localhost        (Nginx → Vue 3 前端)"
    echo "    默认账号:   admin / admin123"
    echo "    首次登录后请立即修改密码！"
else
    echo ""
    echo "💡 启用管理前端:  bash scripts/deploy.sh --web"
fi
echo ""
echo "📊 查看日志:      docker compose logs -f [service_name]"
echo "🛑 停止服务:      docker compose down"
echo "🔄 重启单服务:    docker compose restart app"
