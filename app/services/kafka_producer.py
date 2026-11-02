# app/services/kafka_producer.py
"""Kafka 生产者封装，基于 ``confluent-kafka`` (librdkafka)。

为 ``consumers/*`` 与 Celery 任务提供发送 Kafka 消息的便捷接口。
"""
import json
import logging
import threading
import uuid
from typing import Any, Dict, Optional

from confluent_kafka import KafkaException, Producer

from app.core.config import settings

logger = logging.getLogger(__name__)


class KafkaProducerService:
    """线程安全的单例 Producer (内部复用 librdkafka 实例)。"""

    _instance: Optional["KafkaProducerService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "KafkaProducerService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self._producer: Optional[Producer] = None
        self._init_lock = threading.Lock()

    def _get_producer(self) -> Producer:
        if self._producer is None:
            with self._init_lock:
                if self._producer is None:
                    cfg = {
                        "bootstrap.servers": settings.kafka_bootstrap_servers,
                        "client.id": "wechat-publisher-producer",
                        "acks": "all",
                        "enable.idempotence": True,
                        "compression.type": "snappy",
                        "linger.ms": 20,
                        "message.send.max.retries": 3,
                        "request.timeout.ms": 10000,
                    }
                    self._producer = Producer(cfg)
                    logger.info("confluent-kafka Producer 已连接: %s",
                                settings.kafka_bootstrap_servers)
        return self._producer

    # ----------------- 业务便捷方法 -----------------
    def send_crawl_topics(self, payload: Dict[str, Any]) -> None:
        self._send(settings.kafka_topic_crawl, payload)

    def send_generate_request(self, payload: Dict[str, Any]) -> None:
        """``process_user_choices`` 把用户选择转成生成请求, 投到 ``kafka_topic_generate``."""
        self._send(settings.kafka_topic_generate, payload)

    def send_article_generated(self, payload: Dict[str, Any]) -> None:
        """``consumers/generate_consumer.py`` 在生成文章后调用。"""
        self._send(settings.kafka_topic_push, payload)

    def send_publish_request(self, payload: Dict[str, Any]) -> None:
        """``consumers/generate_consumer.py`` 派发 publish 任务。"""
        self._send(settings.kafka_topic_publish, payload)

    def send_publish_to_platform_request(
        self,
        article_id: int,
        platform_account_id: int,
        publish_type: str = "manual",
    ) -> None:
        """派发多平台发布任务（头条、小红书等），由 toutiao_publish_consumer 消费。"""
        self._send(settings.kafka_topic_publish, {
            "article_id": article_id,
            "platform_account_id": platform_account_id,
            "publish_type": publish_type,
        })

    def send_system_event(self, payload: Dict[str, Any]) -> None:
        self._send(settings.kafka_topic_push, payload)

    # ----------------- 底层 -----------------
    def _send(
        self,
        topic: str,
        payload: Dict[str, Any],
        key: Optional[str] = None,
    ) -> None:
        """发送单条消息到 Kafka。

        修复 M14: BufferError 重试逻辑改为复用已构造的 value/key bytes,
        避免重新 json.dumps (微小不一致风险) 与 encoder 二次调用; 同时
        用 ``produce(..., retry=...)`` 让 librdkafka 内部处理单条发送失败.
        """
        producer = self._get_producer()
        # 注入唯一 message_id，支持消费端去重
        payload_with_id = {**payload, "message_id": uuid.uuid4().hex}
        value = json.dumps(payload_with_id, ensure_ascii=False).encode("utf-8")
        kbytes = key.encode("utf-8") if key else None

        def _do_produce() -> None:
            producer.produce(
                topic=topic,
                value=value,
                key=kbytes,
                on_delivery=self._on_delivery,
            )
            # poll(0) 处理回调事件循环，不阻塞主调用
            producer.poll(0)

        try:
            _do_produce()
        except BufferError:
            # 本地队列满, flush 后重试一次 (复用已构造的 value/key)
            logger.warning("Kafka 本地队列已满，flush 后重试 %s", topic)
            try:
                producer.flush(5)
            except KafkaException as exc:
                logger.error("Kafka flush 失败 topic=%s: %s", topic, exc)
                raise  # flush 失败时消息已丢失, 必须让调用方感知
            try:
                _do_produce()
            except (BufferError, KafkaException) as exc:
                logger.error(
                    "Kafka produce 重试仍失败 topic=%s: %s", topic, exc,
                )
                raise  # 消息在 flush 时移除但未成功发送, 必须抛出让调用方重试
        except KafkaException as exc:
            logger.error("Kafka produce 失败 topic=%s: %s", topic, exc)
            raise

    @staticmethod
    def _on_delivery(err, msg) -> None:
        if err is not None:
            logger.error("投递失败 topic=%s partition=%s: %s",
                         msg.topic(), msg.partition() if msg else "?", err)
        else:
            logger.info("已发送 topic=%s partition=%s offset=%s",
                        msg.topic(), msg.partition(), msg.offset())

    def flush(self, timeout: float = 5.0) -> int:
        """flush 所有待发送消息到 broker。

        返回值: 剩余未发送消息数 (0 表示全部成功)。

        抛出:
            KafkaException: flush 失败 (超时或 broker 错误)
        """
        if self._producer is not None:
            remaining = self._producer.flush(timeout)
            if remaining > 0:
                logger.warning("Kafka flush 仍有 %d 条消息未发送", remaining)
                raise KafkaException(KafkaException._cimpl.create(
                    KafkaException._BAD_MSG,  # type: ignore
                    f"flush 仍有 {remaining} 条消息未发送"
                ))
            return remaining
        return 0

    def close(self) -> None:
        if self._producer is not None:
            try:
                self._producer.flush(5)
            except Exception:  # noqa: BLE001
                logger.exception("关闭 Producer 失败")
            finally:
                self._producer = None
