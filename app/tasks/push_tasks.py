# app/tasks/push_tasks.py
"""对话题进行批量推送的 Celery 任务。

调度策略见 ``app/tasks/celery_app.py`` 的 ``beat_schedule``。推送逻辑委托
给 ``PushNotificationService``。
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.config import settings, current_datetime, today_start
from app.core.database import session_scope
from app.core.pii import hash_openid  # #10
from app.models.topic import HotTopic, TopicStatus
from app.models.user import UserSubscription
from app.models.platform import PlatformAccount
from app.services.kafka_producer import KafkaProducerService
from app.tasks.celery_app import celery_app
from app.core.runtime_overrides import apply_runtime_overrides

logger = logging.getLogger(__name__)


@dataclass
class TopicPushResult:
    """线程安全的结果收集对象（不含 ORM 对象）。"""
    topic_id: int
    success_count: int
    total_count: int


def _send_template(openid: str, topic_title: str) -> bool:
    """发送客服消息通知（委托给 PushNotificationService）。"""
    from app.services.push_notification import PushNotificationService
    if not settings.wechat_enable_custom_message:
        return False
    svc = PushNotificationService()
    return svc.send(
        user_openid=openid,
        article_id=None,
        title=topic_title,
        publish_status="draft",
    )


def _send_to_account(account_platform_type: str, openid: str, topic_title: str) -> bool:
    """发送客服消息通知（委托给 PushNotificationService）。"""
    from app.services.push_notification import PushNotificationService
    if account_platform_type != "wechat":
        return False
    svc = PushNotificationService()
    return svc.send(
        user_openid=openid,
        article_id=None,
        title=topic_title,
        publish_status="draft",
    )


@celery_app.task(bind=True, max_retries=3)
def push_topics_to_users(self):
    """读取今日待推送话题, 发送给活跃订阅用户（全局版本）"""
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    if not settings.enable_wechat_push:
        logger.info("微信推送开关关闭, 跳过本轮推送")
        return {"skipped": True}

    logger.info("开始推送今日话题（全局）")
    producer = KafkaProducerService()

    try:
        with session_scope() as db:
            today = today_start().date()
            limit = settings.scheduler_topic_limit
            topics = (
                db.query(HotTopic)
                .filter(
                    HotTopic.status == TopicStatus.PENDING,
                    HotTopic.create_time >= today,
                    HotTopic.platform_account_id.is_(None),  # 仅全局话题
                )
                .order_by(HotTopic.create_time.desc())
                .limit(limit)
                .all()
            )

            if not topics:
                logger.info("今日无可推送话题（全局）")
                return {"pushed": 0, "topics": 0}

            interact_cutoff = current_datetime() - timedelta(hours=settings.wechat_message_window_hours)
            rows = (
                db.query(UserSubscription.openid)
                .filter(
                    UserSubscription.is_active.is_(True),
                    UserSubscription.last_interact_time >= interact_cutoff,
                )
                .limit(settings.push_user_batch_size)
                .all()
            )
            target_openids = [r[0] for r in rows]

            if not target_openids:
                logger.info("48h 窗口内没有活跃用户, 跳过本轮推送")
                return {"pushed": 0, "topics": len(topics), "users": 0, "reason": "no users in 48h window"}

            # 收集 topic 元数据，避免 ORM 对象跨线程
            topic_data = [(t.id, t.title, t.status == TopicStatus.PENDING) for t in topics]

            # 小延迟防止触发微信限流 (每条消息间隔 50ms)
            _SEND_DELAY = 0.05

            def _send_for_topic(topic_id: int, title: str) -> TopicPushResult:
                """为一个话题向所有用户发送，返回结果。"""
                success_count = 0
                for openid in target_openids:
                    ok = _send_template(openid, title)
                    if ok:
                        success_count += 1
                    logger.info(
                        "推送结果: user=%s topic_id=%d title=%s success=%s",
                        hash_openid(openid), topic_id, title[:30], ok,
                    )
                    time.sleep(_SEND_DELAY)
                return TopicPushResult(topic_id=topic_id, success_count=success_count, total_count=len(target_openids))

            results: list[TopicPushResult] = []
            with ThreadPoolExecutor(max_workers=min(len(topics), 8)) as pool:
                futures = {pool.submit(_send_for_topic, tid, title): tid for tid, title, _ in topic_data}
                for future in as_completed(futures):
                    results.append(future.result())

            # 主线程更新 ORM（线程安全）
            topic_map = {t.id: t for t in topics}
            for r in results:
                topic = topic_map[r.topic_id]
                producer.send_system_event({
                    "type": "topic_pushed",
                    "topic_id": r.topic_id,
                    "title": topic.title,
                    "users_count": r.total_count,
                    "success_count": r.success_count,
                })
                if topic.status == TopicStatus.PENDING:
                    topic.status = TopicStatus.PUSHED
                    topic.push_time = current_datetime()

            total_pushed = sum(r.success_count for r in results)
            db.commit()
            logger.info("推送完成: 话题 %d 条, 发送给 %d 个用户, 成功 %d 次", len(topics), len(target_openids), total_pushed)
            return {"pushed": total_pushed, "topics": len(topics), "users": len(target_openids)}

    except Exception as exc:
        logger.error("推送失败: %s", exc)
        self.retry(exc=exc, countdown=settings.celery_push_retry_countdown)


@celery_app.task(bind=True, max_retries=3)
def push_for_account(self, account_id: str):
    """为指定账号推送话题给用户"""
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    logger.info(f"开始为账号 {account_id} 推送话题")

    try:
        account_uuid = UUID(account_id)
        with session_scope() as db:
            account = db.query(PlatformAccount).filter(
                PlatformAccount.account_id == account_uuid,
                PlatformAccount.is_deleted == False,
                PlatformAccount.is_active == True,
            ).first()

        if not account:
            logger.warning(f"Account {account_id} not found or inactive")
            return {"skipped": True, "reason": "account not found"}

        # 提取所需属性，避免 ORM 对象跨 session 或跨线程
        account_platform_type = account.platform.platform_type

        with session_scope() as db:
            today = today_start().date()
            limit = settings.scheduler_topic_limit
            topics = (
                db.query(HotTopic)
                .filter(
                    HotTopic.status == TopicStatus.PENDING,
                    HotTopic.create_time >= today,
                    HotTopic.platform_account_id == account.id,
                )
                .order_by(HotTopic.create_time.desc())
                .limit(limit)
                .all()
            )

            if not topics:
                logger.info(f"账号 {account_id} 今日无可推送话题")
                return {"pushed": 0, "topics": 0}

            interact_cutoff = current_datetime() - timedelta(hours=settings.wechat_message_window_hours)
            rows = (
                db.query(UserSubscription.openid)
                .filter(
                    UserSubscription.is_active.is_(True),
                    UserSubscription.last_interact_time >= interact_cutoff,
                )
                .limit(settings.push_user_batch_size)
                .all()
            )
            target_openids = [r[0] for r in rows]

            if not target_openids:
                return {"pushed": 0, "topics": len(topics), "users": 0, "reason": "no users in 48h window"}

            # 收集 topic 元数据，避免 ORM 对象跨线程
            topic_data = [(t.id, t.title, t.status == TopicStatus.PENDING) for t in topics]

            _SEND_DELAY = 0.05

            def _send_for_topic(topic_id: int, title: str) -> TopicPushResult:
                """为一个话题向所有用户发送，返回结果。"""
                success_count = 0
                for openid in target_openids:
                    ok = _send_to_account(account_platform_type, openid, title)
                    if ok:
                        success_count += 1
                    logger.info(
                        "账号 %s 推送结果: user=%s topic_id=%d success=%s",
                        account_id, hash_openid(openid), topic_id, ok,
                    )
                    time.sleep(_SEND_DELAY)
                return TopicPushResult(topic_id=topic_id, success_count=success_count, total_count=len(target_openids))

            results: list[TopicPushResult] = []
            with ThreadPoolExecutor(max_workers=min(len(topics), 8)) as pool:
                futures = {pool.submit(_send_for_topic, tid, title): tid for tid, title, _ in topic_data}
                for future in as_completed(futures):
                    results.append(future.result())

            # 主线程更新 ORM（线程安全）
            topic_map = {t.id: t for t in topics}
            for r in results:
                topic = topic_map[r.topic_id]
                if topic.status == TopicStatus.PENDING:
                    topic.status = TopicStatus.PUSHED
                    topic.push_time = current_datetime()

            total_pushed = sum(r.success_count for r in results)
            db.commit()
            logger.info("账号 %s 推送完成: 话题 %d 条, 成功 %d 次", account_id, len(topics), total_pushed)
            return {"pushed": total_pushed, "topics": len(topics), "users": len(target_openids)}

    except Exception as exc:
        logger.error("账号 %s 推送失败: %s", account_id, exc)
        self.retry(exc=exc, countdown=settings.celery_push_retry_countdown)
