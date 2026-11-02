# app/services/push_notification.py
"""微信公众号推送通知 Service。

将推送通知逻辑从 Kafka Consumer 中提取出来，形成统一的 Service 供
Celery 任务 / Kafka Consumer / API 层复用。

- 48h 窗口预检：查 user_subscriptions.last_interact_time
- 客服消息开关检查：wechat_enable_custom_message
- 发送 system_event 到 Kafka 记录推送结果
"""
import logging
from datetime import timedelta
from typing import Optional

from app.core.config import current_datetime, settings
from app.core.database import session_scope
from app.core.pii import hash_openid
from app.models.user import UserSubscription
from app.services.kafka_producer import KafkaProducerService
from app.services.wechat import WeChatService

logger = logging.getLogger(__name__)


class PushNotificationService:
    """微信公众号推送通知 Service（同步）。"""

    def __init__(self) -> None:
        self.wechat = WeChatService()
        self.producer = KafkaProducerService()

    def send(
        self,
        user_openid: str,
        article_id: Optional[int],
        title: str,
        publish_status: str = "draft",
    ) -> bool:
        """发送微信客服消息通知。

        Args:
            user_openid: 用户 openid
            article_id: 文章 ID（可为 None）
            title: 文章标题
            publish_status: "published" | "draft"

        Returns:
            True 推送成功，False 跳过或失败。
        """
        # 参数校验
        if not user_openid or not title:
            logger.warning(
                "推送通知参数不全: user=%s title=%s",
                hash_openid(user_openid),
                title,
            )
            return False

        # 客服消息开关检查
        if not settings.wechat_enable_custom_message:
            logger.info(
                "客服消息已禁用 (wechat_enable_custom_message=false), 跳过: user=%s",
                hash_openid(user_openid),
            )
            self._send_system_event({
                "type": "push_skipped_disabled",
                "user_openid": user_openid,
                "article_id": article_id,
            })
            return False

        # 48h 窗口预检
        if not self._user_in_48h_window(user_openid):
            logger.info(
                "用户不在 48h 互动窗口, 跳过推送: user=%s article=%s",
                hash_openid(user_openid),
                article_id,
            )
            self._send_system_event({
                "type": "push_skipped_48h_window",
                "user_openid": user_openid,
                "article_id": article_id,
            })
            return False

        # 构造消息内容
        if publish_status == "published":
            content = (
                f"✅ 您选择的文章已发布!\n"
                f"标题: {title}\n"
                f"请前往公众号查看"
            )
        else:
            content = (
                f"📝 您的文章已生成并保存为草稿!\n"
                f"标题: {title}\n"
                f"请前往公众号后台发布"
            )

        # 发送客服消息
        try:
            success = self.wechat.send_custom_message(user_openid, content)
            if success:
                logger.info("推送成功: %s", hash_openid(user_openid))
                self._send_system_event({
                    "type": "push_success",
                    "user_openid": user_openid,
                    "article_id": article_id,
                })
                return True
            else:
                logger.error(
                    "推送失败 (用户可能不在 48h 窗口): %s",
                    hash_openid(user_openid),
                )
                self._send_system_event({
                    "type": "push_failed",
                    "user_openid": user_openid,
                    "article_id": article_id,
                })
                return False
        except Exception:  # noqa: BLE001
            logger.exception("处理推送异常: user=%s", hash_openid(user_openid))
            self._send_system_event({
                "type": "push_failed",
                "user_openid": user_openid,
                "article_id": article_id,
            })
            return False

    @staticmethod
    def _user_in_48h_window(user_openid: str) -> bool:
        """查 user_subscriptions.last_interact_time，距今 > 48h 则 False。

        找不到记录（用户没关注过）也返回 False。
        DB 出错时 fail-closed，返回 False 让消息重试。
        """
        try:
            with session_scope() as db:
                row = (
                    db.query(UserSubscription.last_interact_time)
                    .filter(UserSubscription.openid == user_openid)
                    .first()
                )
                if row is None or row[0] is None:
                    return False
                cutoff = current_datetime() - timedelta(
                    hours=settings.wechat_message_window_hours
                )
                return row[0] >= cutoff
        except Exception:  # noqa: BLE001
            logger.exception("48h 窗口预检失败, 拒绝推送等待重试")
            return False

    def _send_system_event(self, payload: dict) -> None:
        """发送 system_event 到 Kafka，失败只记录日志不抛异常。"""
        try:
            self.producer.send_system_event(payload)
        except Exception:  # noqa: BLE001
            logger.exception("记录 system_event 失败: %s", payload.get("type"))
