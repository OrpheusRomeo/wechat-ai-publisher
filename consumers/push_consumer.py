# consumers/push_consumer.py
"""Kafka 推送消费者 (基于 confluent-kafka)。

订阅 ``kafka_topic_push``，调用 PushNotificationService 发送客服消息。
"""
import json
import logging

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.core.config import settings
from app.core.runtime_overrides import apply_runtime_overrides
from app.core.pii import hash_openid
from app.services.kafka_producer import KafkaProducerService
from consumers._dlq import DeadLetterHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONSUMER_GROUP = f"{settings.kafka_consumer_group}-push"


def _build_consumer() -> Consumer:
    return Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": CONSUMER_GROUP,
        "client.id": "wechat-consumer-push",
        "auto.offset.reset": "earliest",
        # 关闭 auto-commit, 由 process_message 成功返回后手动 commit
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        # 协作式 sticky 分区分配，减少 rebalance 时的服务中断
        "partition.assignment.strategy": "range",
        # 增加处理 timeout，避免长任务被误判为失败
        "max.poll.interval.ms": 600000,  # 10 分钟
        "session.timeout.ms": 60000,       # 1 分钟
    })


class PushConsumer:
    def __init__(self) -> None:
        self.topic = settings.kafka_topic_push
        self.consumer: Consumer = _build_consumer()
        self.dlq = DeadLetterHandler()

    def start(self) -> None:
        self.consumer.subscribe([self.topic])
        logger.info("启动推送消费者: %s (group=%s)", self.topic, CONSUMER_GROUP)
        try:
            while True:
                msg = self.consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    err = msg.error()
                    if err.code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("消费者错误: %s", err)
                    if not err.retriable():
                        break
                    continue
                try:
                    value = json.loads(msg.value().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    # 修复 M12: 编码错误转 DLQ
                    logger.warning("消息格式错误, 转 DLQ: %s", exc)
                    self.dlq.dead_letter(
                        msg.topic(), msg.partition(), msg.offset(),
                        msg.value(), f"json decode error: {exc}",
                    )
                    self._commit(msg)
                    self.dlq.cleanup(msg.topic(), msg.partition(), msg.offset())
                    continue
                try:
                    self.process_message(value, msg)
                except Exception as exc:  # noqa: BLE001
                    attempts = self.dlq.record_failure(
                        msg.topic(), msg.partition(), msg.offset(),
                    )
                    logger.error(
                        "处理消息失败 (attempt %d/%d): %s",
                        attempts, settings.kafka_consumer_max_attempts, exc,
                    )
                    if self.dlq.should_dead_letter(
                        msg.topic(), msg.partition(), msg.offset()
                    ):
                        self.dlq.dead_letter(
                            msg.topic(), msg.partition(), msg.offset(),
                            msg.value(), f"max attempts reached: {exc}",
                        )
                        self._commit(msg)
                        self.dlq.cleanup(msg.topic(), msg.partition(), msg.offset())
                    continue
                self._commit(msg)
                self.dlq.cleanup(msg.topic(), msg.partition(), msg.offset())
        except KeyboardInterrupt:
            logger.info("消费者收到中断, 正在停止...")
        finally:
            try:
                self.consumer.close()
            except Exception:  # noqa: BLE001
                logger.exception("关闭 Consumer 失败")

    def _commit(self, msg) -> None:
        try:
            self.consumer.commit(message=msg, asynchronous=False)
        except KafkaException:
            logger.exception(
                "commit offset 失败 (topic=%s partition=%s offset=%s), "
                "下次启动可能重发",
                msg.topic(), msg.partition(), msg.offset(),
            )

    def process_message(self, data: dict, msg) -> None:
        # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
        apply_runtime_overrides()
        from app.services.push_notification import PushNotificationService
        try:
            svc = PushNotificationService()
            svc.send(
                user_openid=data.get("user_openid"),
                article_id=data.get("article_id"),
                title=data.get("title"),
                publish_status=data.get("publish_status", "draft"),
            )
        except Exception as exc:
            # 发送失败不重试（48h 窗口过期等业务错误，重试也无用）
            logger.warning(
                "推送失败（不重试）: user=%s title=%s err=%s",
                hash_openid(data.get("user_openid", "")),
                data.get("title", "")[:20],
                exc,
            )

if __name__ == "__main__":
    PushConsumer().start()
