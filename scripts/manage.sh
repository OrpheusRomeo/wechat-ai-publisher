#!/bin/bash
# scripts/manage.sh - 日常运维：查看日志、重启、清理、shell 进入容器
set -e

SERVICE="${1:-app}"

case "$2" in
  logs)
    docker compose logs -f "$SERVICE"
    ;;
  restart)
    docker compose restart "$SERVICE"
    ;;
  stop)
    docker compose stop "$SERVICE"
    ;;
  start)
    docker compose start "$SERVICE"
    ;;
  shell)
    docker compose exec "$SERVICE" /bin/sh
    ;;
  ps)
    docker compose ps "$SERVICE"
    ;;
  rm)
    docker compose rm -f "$SERVICE"
    ;;
  *)
    echo "用法: $0 <service> {logs|restart|stop|start|shell|ps|rm}"
    exit 1
    ;;
esac
