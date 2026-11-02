"""Kafka 消费者死信队列 (Dead Letter Queue) 工具 (#修复 M12).

设计:
- 用 ``{topic}:{partition}:{offset}`` 作 key, 记录该消息连续失败次数
- 当连续失败超过 ``settings.kafka_consumer_max_attempts`` 次,
  把原始消息 (含 value 与 headers) 发送到 ``settings.kafka_topic_dlq``,
  并 commit 当前 offset, 让消费者从下一条继续
- 如果 DLQ 发送失败, 抛出异常 (不 commit), 消息会被重新投递

为什么不用 ``{key: count}`` 全局计数?
- 消费者重启后计数清零是 OK 的: 重启后第一次 poll 就会再尝试一次,
  失败 N 次仍然升级 DLQ; 相比用 Kafka headers 更简单, 权衡下来选 in-memory.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.kafka_producer import KafkaProducerService

logger = logging.getLogger(__name__)


class DeadLetterHandler:
    """轻量级 DLQ 策略, 进程内计数."""

    def __init__(
        self,
        max_attempts: Optional[int] = None,
        dlq_topic: Optional[str] = None,
    ) -> None:
        self._attempts: Dict[str, int] = {}
        self._max = max_attempts or settings.kafka_consumer_max_attempts
        self._dlq = dlq_topic or settings.kafka_topic_dlq
        self._producer = KafkaProducerService()

    @staticmethod
    def _key(topic: str, partition: int, offset: int) -> str:
        return f"{topic}:{partition}:{offset}"

    def record_failure(self, topic: str, partition: int, offset: int) -> int:
        """记录一次失败, 返回当前累计次数."""
        k = self._key(topic, partition, offset)
        self._attempts[k] = self._attempts.get(k, 0) + 1
        return self._attempts[k]

    def record_success(self, topic: str, partition: int, offset: int) -> None:
        """成功后清理计数."""
        self._attempts.pop(self._key(topic, partition, offset), None)

    def should_dead_letter(self, topic: str, partition: int, offset: int) -> bool:
        return self._attempts.get(self._key(topic, partition, offset), 0) >= self._max

    def dead_letter(
        self,
        original_topic: str,
        partition: int,
        offset: int,
        raw_value: bytes,
        reason: str,
    ) -> None:
        """把当前消息转入 DLQ, 含原始 payload 与失败原因."""
        try:
            payload = json.loads(raw_value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"_raw_b64": raw_value.hex()}
        envelope = {
            "original_topic": original_topic,
            "partition": partition,
            "offset": offset,
            "reason": reason,
            "ts": int(time.time()),
            "payload": payload,
        }
        # 直接复用 producer._send 避免再加方法; 这里走 send_system_event 兜底
        # DLQ 发送失败时抛出异常，避免消息静默丢失
        try:
            self._producer._send(self._dlq, envelope, key=f"{original_topic}:{offset}")
            logger.warning(
                "[DLQ] 已转死信 %s/%s/%s reason=%s",
                original_topic, partition, offset, reason,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[DLQ] 转死信失败 %s/%s/%s: %s",
                original_topic, partition, offset, exc,
            )
            raise RuntimeError(
                f"DLQ 发送失败，无法保存消息 {original_topic}:{partition}:{offset}"
            ) from exc

    def cleanup(self, topic: str, partition: int, offset: int) -> None:
        """外部成功处理时调用."""
        self.record_success(topic, partition, offset)
