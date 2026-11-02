# app/core/kafka_client.py
"""Kafka Admin 客户端，基于 confluent-kafka (librdkafka)。
``init_topics`` 用于 docker-compose 启动时预创建话题。"""
import logging
from typing import List, Optional

from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.error import KafkaException

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TOPICS: List[str] = [
    settings.kafka_topic_crawl,
    settings.kafka_topic_generate,
    settings.kafka_topic_push,
    settings.kafka_topic_publish,
]


def _split_servers(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def init_topics(
    topics: Optional[List[str]] = None,
    num_partitions: int = 3,
    replication_factor: int = 1,
) -> List[str]:
    """在 Kafka 中预创建话题。

    已存在的话题会自动跳过；返回全部预期话题列表。
    """
    target = topics or DEFAULT_TOPICS
    admin = AdminClient({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "client.id": "wechat-publisher-admin",
        "request.timeout.ms": 10000,
    })

    new_topics = [
        NewTopic(
            topic=t,
            num_partitions=num_partitions,
            replication_factor=replication_factor,
        )
        for t in target
    ]

    try:
        futures = admin.create_topics(new_topics)
    except KafkaException as exc:
        logger.error("创建话题失败: %s", exc)
        return target

    created: List[str] = []
    skipped: List[str] = []
    failed: List[str] = []
    for topic_name, fut in futures.items():
        try:
            fut.result(timeout=15)
            created.append(topic_name)
        except KafkaException as exc:
            # TopicExistsError 在 librdkafka 中归为 KafkaException 内
            err = exc.args[0] if exc.args else None
            from confluent_kafka import KafkaError
            if err is not None and err.code() == KafkaError.TOPIC_ALREADY_EXISTS:
                skipped.append(topic_name)
            else:
                logger.warning("创建话题 %s 失败: %s", topic_name, exc)
                failed.append(topic_name)

    if created:
        logger.info("Kafka 已创建话题: %s", created)
    if skipped:
        logger.info("Kafka 话题已存在，跳过: %s", skipped)
    if failed:
        logger.warning("Kafka 创建失败的话题: %s", failed)

    return target
