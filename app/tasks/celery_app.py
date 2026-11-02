# app/tasks/celery_app.py
import time

from celery import Celery
from celery.beat import PersistentScheduler
from celery.schedules import crontab
from celery.signals import beat_init, worker_init
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    'wechat_publisher',
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        'app.tasks.crawl_tasks',
        'app.tasks.article_tasks',
        'app.tasks.push_tasks'
    ]
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    result_expires=3600,
    timezone=settings.celery_timezone,
    enable_utc=False,
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=100,
)


def _parse_hhmm(s: str) -> tuple:
    """把 ``'08:00'`` 解析为 (8, 0); 解析失败回落 (8, 0)."""
    try:
        hh, mm = s.split(":", 1)
        h = int(hh); m = int(mm)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return 8, 0


def _parse_cron_expr(s: str) -> dict:
    """解析 cron 表达式或 HH:MM 格式，返回 crontab 参数 dict"""
    # HH:MM 格式
    if ":" in s and not any(c in s for c in "*/,"):
        h, m = _parse_hhmm(s)
        return crontab(hour=h, minute=m)

    # 完整 cron 表达式: 分 时 日 月 周
    parts = s.split()
    if len(parts) == 5:
        minute, hour, dom, month, dow = parts
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=dom if dom != '*' else None,
            month_of_year=month if month != '*' else None,
            day_of_week=dow if dow != '*' else None,
        )

    # 默认
    return crontab(hour=8, minute=0)


def _build_beat_schedule() -> dict:
    """修复 M6: 从 ``settings.scheduler_crawl_time`` / ``scheduler_push_time``
    动态生成 cron, ``.env`` 改动时间直接生效 (Celery 启动时读).

    D 方案: 09:00 push-topics-to-users 已弃用 (订阅号无客服消息权限, 推送
    触达率 0). 替换为 09:30 daily-publish-digest (群发一篇日报, 覆盖所有
    粉丝). push 任务代码保留供 ``enable_wechat_push=true`` 的认证号复用.
    """
    crawl_h, crawl_m = _parse_hhmm(settings.scheduler_crawl_time)
    push_h, push_m = _parse_hhmm(settings.scheduler_push_time)

    schedule = {
        'crawl-hot-topics': {
            'task': 'app.tasks.crawl_tasks.crawl_hot_topics',
            'schedule': crontab(hour=crawl_h, minute=crawl_m),
            'options': {'queue': 'crawl'},
        },
        'push-topics-to-users': {
            'task': 'app.tasks.push_tasks.push_topics_to_users',
            'schedule': crontab(hour=push_h, minute=push_m),
            'options': {'queue': 'push'},
        },
        'daily-publish-digest': {
            'task': 'app.tasks.article_tasks.daily_publish_digest',
            'schedule': crontab(hour=9, minute=30),
            'options': {'queue': 'article'},
        },
        'process-user-choices': {
            'task': 'app.tasks.article_tasks.process_user_choices',
            'schedule': crontab(minute='*/30'),
            'options': {'queue': 'article'},
        },
    }

    # 尝试从数据库加载 per-account 调度
    schedule = _add_per_account_schedules(schedule)
    return schedule


def _add_per_account_schedules(base_schedule: dict) -> dict:
    """从数据库加载各账号的自定义调度，动态添加 beat 任务"""
    try:
        from app.core.database import session_scope
        from app.models.platform import PlatformAccount

        with session_scope() as db:
            accounts = db.query(PlatformAccount).filter(
                PlatformAccount.is_deleted == False,
                PlatformAccount.is_active == True,
            ).all()

            for account in accounts:
                # 爬虫调度
                if account.crawl_cron:
                    try:
                        cron = _parse_cron_expr(account.crawl_cron)
                        base_schedule[f'crawl-{account.account_id}'] = {
                            'task': 'app.tasks.crawl_tasks.crawl_for_account',
                            'schedule': cron,
                            'options': {'queue': 'crawl'},
                            'kwargs': {'account_id': str(account.account_id)},
                        }
                    except Exception as e:
                        logger.warning(f"Invalid crawl_cron for account {account.account_id}: {e}")

                # 推送调度
                if account.push_cron:
                    try:
                        cron = _parse_cron_expr(account.push_cron)
                        base_schedule[f'push-{account.account_id}'] = {
                            'task': 'app.tasks.push_tasks.push_for_account',
                            'schedule': cron,
                            'options': {'queue': 'push'},
                            'kwargs': {'account_id': str(account.account_id)},
                        }
                    except Exception as e:
                        logger.warning(f"Invalid push_cron for account {account.account_id}: {e}")

    except Exception as e:
        logger.warning(f"Failed to load per-account schedules: {e}")

    return base_schedule


def reload_beat_schedule():
    """重新加载 beat schedule（在账号增删改后调用）"""
    celery_app.conf.beat_schedule = _build_beat_schedule()
    logger.info("Beat schedule reloaded")


# ---------------------------------------------------------------------------
# 运行时配置覆盖（system_settings → settings）
# ---------------------------------------------------------------------------
# 管理后台「系统配置」把值写在 system_settings 表里，运行时由
# app.core.runtime_overrides 覆盖到 settings 单例上。worker / beat 启动时
# 应用一次，之后每个任务开始前再应用一次（带 TTL 缓存，不会频繁查库）。

def _apply_runtime_settings(reload_schedule: bool = False) -> None:
    """把 system_settings 覆盖到 settings（失败不阻断启动）。"""
    try:
        from app.core.runtime_overrides import apply_runtime_overrides
        apply_runtime_overrides(force=True)
        if reload_schedule:
            # 调度时间可能被改过，重建 beat schedule
            reload_beat_schedule()
    except Exception:  # noqa: BLE001
        logger.warning("应用 system_settings 运行时覆盖失败", exc_info=True)


@worker_init.connect
def _on_worker_init(**_kwargs) -> None:
    _apply_runtime_settings()


@beat_init.connect
def _on_beat_init(**_kwargs) -> None:
    _apply_runtime_settings(reload_schedule=True)


class RuntimeSettingsScheduler(PersistentScheduler):
    """让 beat 定期按最新的管理后台配置重建调度表。

    Celery 的 beat 默认只在**启动时**把 ``app.conf.beat_schedule`` 合并进
    调度器，之后管理后台改了调度时间也不会生效。这个子类在每次 tick 里
    （最多每 ``reload_every`` 秒一次）做一次：

        读 system_settings → 重建 app.conf.beat_schedule → 合并回调度器

    失败时保持原调度表不动，不影响正在跑的任务。
    """

    reload_every = 60  # 秒

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_reload = 0.0

    def tick(self, *args, **kwargs):
        now = time.monotonic()
        if now - self._last_reload >= self.reload_every:
            self._last_reload = now
            self._reload_runtime_schedule()
        return super().tick(*args, **kwargs)

    def _reload_runtime_schedule(self) -> None:
        try:
            from app.core.runtime_overrides import apply_runtime_overrides
            apply_runtime_overrides(force=True)
            reload_beat_schedule()
            self.merge_inplace(self.app.conf.beat_schedule)
            logger.info("已按最新配置刷新 beat 调度表")
        except Exception:  # noqa: BLE001
            logger.warning("刷新 beat 调度表失败, 保持原调度表", exc_info=True)


# beat 使用自定义调度器, 让管理后台的调度时间能热生效
celery_app.conf.beat_scheduler = "app.tasks.celery_app:RuntimeSettingsScheduler"


# 定时任务
celery_app.conf.beat_schedule = _build_beat_schedule()

# 队列路由
celery_app.conf.task_routes = {
    'app.tasks.crawl_tasks.*': {'queue': 'crawl'},
    'app.tasks.article_tasks.*': {'queue': 'article'},
    'app.tasks.push_tasks.*': {'queue': 'push'},
}

