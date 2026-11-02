#!/bin/bash
# scripts/entrypoint.sh - 应用容器入口：根据传入的第一个参数决定启动哪个进程
set -e

CMD="${1:-app}"

case "$CMD" in
  app)
    python -c "from app.core.database import init_db; init_db()"
    python -c "from app.core.kafka_client import init_topics; init_topics()"
    exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8888}" \
      --workers "${WORKER_CONCURRENCY:-4}" \
      --log-level "$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"
    ;;
  celery-worker)
    exec celery -A app.tasks.celery_app worker \
      --loglevel="${LOG_LEVEL:-info}" \
      --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
      --pool="${CELERY_WORKER_POOL:-threads}" \
      --queues=crawl,article,push,default \
      --hostname=worker@%h \
      --without-gossip --without-mingle --without-heartbeat
    ;;
  celery-beat)
    exec celery -A app.tasks.celery_app beat \
      --loglevel="${LOG_LEVEL:-info}"
    ;;
  consumer-generate)
    exec python -m consumers.generate_consumer
    ;;
  consumer-push)
    exec python -m consumers.push_consumer
    ;;
  *)
    echo "Unknown CMD: $CMD"; exit 1;;
esac
