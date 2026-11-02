"""Kafka 文章发布消费者 (基于 confluent-kafka)。

订阅 ``kafka_topic_publish``，调用 ArticlePublishingService 发布文章。
"""
import json
import logging

from confluent_kafka import Consumer, KafkaError, KafkaException

from app.core.config import settings
from app.core.runtime_overrides import apply_runtime_overrides
from consumers._dlq import DeadLetterHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONSUMER_GROUP = f"{settings.kafka_consumer_group}-publish"


def _build_consumer() -> Consumer:
    return Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": CONSUMER_GROUP,
        "client.id": "wechat-consumer-publish",
        "auto.offset.reset": "earliest",
        # 关闭 auto-commit, 由 process_message 成功返回后手动 commit
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        # range 分配策略，兼容旧版 librdkafka (cooperative.sticky 需要较新版本)
        "partition.assignment.strategy": "range",
        # 增加处理超时时间，避免长任务被误判为失败
        "max.poll.interval.ms": 600000,  # 10 分钟
        "session.timeout.ms": 60000,       # 1 分钟
        "fetch.message.max.bytes": 1048576,
    })


class PublishConsumer:
    def __init__(self) -> None:
        self.topic = settings.kafka_topic_publish
        self.consumer: Consumer = _build_consumer()
        self.dlq = DeadLetterHandler()

    def start(self) -> None:
        self.consumer.subscribe([self.topic])
        logger.info(
            "启动文章发布消费者: %s (group=%s)", self.topic, CONSUMER_GROUP,
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

                try:
                    self.process_message(value, msg)
                except ValueError as exc:
                    # ValueError = 业务层不可重试错误 (article_id 为 None / 文章不存在)
                    # 这类消息不应无限 redeliver，commit 跳过它们
                    logger.error("业务错误 (跳过消息，不重试): %s", exc)
                    self._commit(msg)
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
                    # 未超阈值: 不 commit, 等下次 poll 重试
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
        """同步提交单条消息的 offset+1, 失败仅记录不抛出."""
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
        from app.services.article_publishing import ArticlePublishingService

        article_id = data.get("article_id")
        if article_id is None:
            # article_id 为 None 是 poison message，无法处理直接跳过
            logger.warning("消息缺少 article_id, 跳过: %s", data)
            self._commit(msg)
            return

        svc = ArticlePublishingService()
        result = svc.publish(
            article_id=article_id,
            publish_type=data.get("publish_type", "auto"),
        )

        # 根据返回结果决定是否需要 commit（业务正常结束时需要 commit）
        # locked/skip/quota_exceeded: 正常业务退出，不需要重试
        # error: 服务内部异常，已记录错误，不重试
        if result.get("status") in ("locked", "skip", "quota_exceeded", "error"):
            self._commit(msg)


if __name__ == "__main__":
    PublishConsumer().start()
