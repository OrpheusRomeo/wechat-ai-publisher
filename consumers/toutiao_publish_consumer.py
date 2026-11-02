# consumers/toutiao_publish_consumer.py
"""多平台文章发布消费者 (基于 confluent-kafka)。

订阅 ``kafka_topic_publish``，对带有 platform_account_id 的消息
调用 ArticlePublishingService.publish_to_platform() 完成发布。

此消费者与 WeChat PublishConsumer (kafka_topic_publish) 共存：
- 无 platform_account_id 的消息 → WeChat PublishConsumer 处理
- 有 platform_account_id 的消息 → 本消费者处理

每个平台账号可配置独立的 cron 调度，实现"不同账号发不同文章"的需求。
"""
import json
import logging

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.core.config import settings
from app.core.runtime_overrides import apply_runtime_overrides
from consumers._dlq import DeadLetterHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 使用独立的 consumer group，offset 由本消费者自己管理
CONSUMER_GROUP = f"{settings.kafka_consumer_group}-toutiao-publish"


def _build_consumer() -> Consumer:
    return Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": CONSUMER_GROUP,
        "client.id": "toutiao-consumer-publish",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "partition.assignment.strategy": "range",
        "max.poll.interval.ms": 600000,
        "session.timeout.ms": 60000,
        "fetch.message.max.bytes": 1048576,
    })


class ToutiaoPublishConsumer:
    """多平台发布消费者（头条、小红书等）"""

    def __init__(self) -> None:
        self.topic = settings.kafka_topic_publish
        self.consumer: Consumer = _build_consumer()
        self.dlq = DeadLetterHandler()

    def start(self) -> None:
        self.consumer.subscribe([self.topic])
        logger.info(
            "启动多平台发布消费者: topic=%s group=%s",
            self.topic, CONSUMER_GROUP,
        )
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
                    logger.warning("消息格式错误, 转 DLQ: %s", exc)
                    self.dlq.dead_letter(
                        msg.topic(), msg.partition(), msg.offset(),
                        msg.value(), f"json decode error: {exc}",
                    )
                    self._commit(msg)
                    self.dlq.cleanup(msg.topic(), msg.partition(), msg.offset())
                    continue

                # 只处理带有 platform_account_id 的消息（多平台发布）
                # 无 platform_account_id 的消息由 WeChat PublishConsumer 处理
                platform_account_id = value.get("platform_account_id")
                if not platform_account_id:
                    # 不是本消费者职责，提交后跳过
                    self._commit(msg)
                    self.dlq.cleanup(msg.topic(), msg.partition(), msg.offset())
                    continue

                try:
                    self.process_message(value, msg)
                except ValueError as exc:
                    # 业务层不可重试错误，直接跳过
                    logger.error("业务错误 (跳过，不重试): %s", exc)
                    self._commit(msg)
                except Exception as exc:
                    attempts = self.dlq.record_failure(
                        msg.topic(), msg.partition(), msg.offset(),
                    )
                    logger.error(
                        "处理消息失败 (attempt %d/%d): %s",
                        attempts, settings.kafka_consumer_max_attempts, exc,
                    )
                    if self.dlq.should_dead_letter(
                        msg.topic(), msg.partition(), msg.offset(),
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
            logger.info("消费者收到中断，正在停止...")
        finally:
            try:
                self.consumer.close()
            except Exception:
                logger.exception("关闭 Consumer 失败")

    def _commit(self, msg) -> None:
        try:
            self.consumer.commit(message=msg, asynchronous=False)
        except KafkaException:
            logger.exception("commit offset 失败")

    def process_message(self, data: dict, msg) -> None:
        # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
        apply_runtime_overrides()
        from app.services.article_publishing import ArticlePublishingService

        article_id = data.get("article_id")
        platform_account_id = data.get("platform_account_id")

        if article_id is None:
            logger.warning("消息缺少 article_id，跳过: %s", data)
            return

        if platform_account_id is None:
            logger.warning("消息缺少 platform_account_id，跳过: %s", data)
            return

        logger.info(
            "多平台发布: article_id=%s platform_account_id=%s",
            article_id, platform_account_id,
        )

        svc = ArticlePublishingService()
        result = svc.publish_to_platform(
            article_id=article_id,
            platform_account_id=platform_account_id,
        )

        status = result.get("status")
        if status == "success":
            logger.info(
                "多平台发布成功: article_id=%s platform=%s media_id=%s",
                article_id,
                result.get("platform_type", ""),
                result.get("platform_media_id", ""),
            )
        else:
            logger.warning(
                "多平台发布失败: article_id=%s status=%s errmsg=%s",
                article_id, status, result.get("errmsg", ""),
            )


if __name__ == "__main__":
    ToutiaoPublishConsumer().start()
