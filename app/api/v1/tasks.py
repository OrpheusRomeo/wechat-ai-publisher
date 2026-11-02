# app/api/v1/tasks.py
"""Celery 任务状态查询。

前端拿到 ``task_id`` 后可以轮询 ``GET /api/v1/tasks/{task_id}`` 直到
``state=SUCCESS`` / ``FAILURE``，避免前端盲目 ``setTimeout`` 抢跑
（爬虫 / DeepSeek 任务通常 10-60 秒，硬编码 3 秒会看到爬取前数据）。

``state`` 取自 :class:`celery.result.AsyncResult`，取值:
- PENDING : 任务已入队但还没被 worker 拾起
- STARTED : worker 已开始执行
- SUCCESS : 成功，``result`` 字段含任务返回值
- FAILURE : 异常，``error`` 字段含异常字符串
- RETRY   : 重试中（``self.retry()`` 触发）
- REVOKED : 被取消
"""
import os
import signal
import logging
from fastapi import APIRouter, Depends

from app.core.auth import require_admin_auth, require_ip_whitelist
from app.tasks.celery_app import celery_app, reload_beat_schedule
from celery.result import AsyncResult

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_admin_auth), Depends(require_ip_whitelist)],
)


@router.get("/{task_id}")
def get_task_status(task_id: str):
    """查询 Celery 任务状态。"""
    result = AsyncResult(task_id, app=celery_app)
    payload: dict = {
        "task_id": task_id,
        "state": result.state,
    }
    if result.state == "SUCCESS":
        # 任务返回的 dict/list/str/int 都直接序列化
        payload["result"] = result.result
    elif result.state == "FAILURE":
        # result.result 是异常实例，转字符串避免序列化问题
        payload["error"] = str(result.result)
    elif result.state == "RETRY":
        try:
            payload["error"] = str(result.result)
        except Exception:
            pass
    # PENDING / STARTED / REVOKED 只返回 state
    return payload


@router.post("/celery/reload-beat")
def reload_celery_beat():
    """重新加载 Celery Beat 调度计划。

    当平台账号的 crontab 变更后，需要调用此接口使新的调度时间生效。
    实现方式：向 celery-beat 进程发送 SIGUSR1 信号，触发 schedule 重载。
    """
    # 在容器内，celery-beat 的 PID 文件在 /var/lib/celerybeat/celerybeat.pid
    pid_file = "/var/lib/celerybeat/celerybeat.pid"

    try:
        # 先重载内存中的配置
        reload_beat_schedule()

        # 再通知 beat 进程重载
        if os.path.exists(pid_file):
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())

            # SIGUSR1 = 信号 10，Celery beat 收到后会重新读取 schedule
            os.kill(pid, signal.SIGUSR1)
            logger.info(f"Celery beat reload signal sent to PID {pid}")
            return {"status": "success", "message": "Celery beat schedule reloaded", "pid": pid}
        else:
            # PID 文件不存在，返回成功（可能是容器刚启动）
            return {"status": "success", "message": "Beat schedule updated (PID file not found, may need restart)", "pid": None}

    except FileNotFoundError:
        return {"status": "warning", "message": "PID file not accessible", "pid": None}
    except ProcessLookupError:
        logger.warning(f"Celery beat process not found")
        return {"status": "warning", "message": "Celery beat process not found, may need container restart"}
    except PermissionError:
        logger.error(f"Permission denied to send signal to celery-beat")
        return {"status": "error", "message": "Permission denied, try restarting celery-beat container"}
    except Exception as e:
        logger.error(f"Failed to reload celery beat: {e}")
        return {"status": "error", "message": str(e)}