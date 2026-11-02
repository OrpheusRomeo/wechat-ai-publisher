# app/services/article_generation.py
"""文章生成服务：聚合 ``generate_consumer.py`` 与 ``article_tasks.py`` 的生成逻辑。

同一份业务有两条执行路径：
- Celery Task: ``app/tasks/article_tasks.generate_article``
- Kafka Consumer: ``consumers/generate_consumer.process_message``

两者在文章生成上的逻辑完全一致，现在统一收敛到本 Service：

1. 校验话题存在
2. 幂等检查：同一 (topic_id, choice_id) 已存在文章 → 直接返回复用
3. 调用 DeepSeek 生成正文
4. 写入 ``GeneratedArticle``（status=GENERATED）
5. 标记 ``UserChoice.is_processed``（如果传了 choice_id）
6. 发送 ``article_generated`` 通知到 Kafka（通知下游推送/发布）
7. 若 ``enable_auto_publish`` 为真，触发自动发布

本 Service 为**同步**函数，供 Celery 任务直接调用。
Kafka Consumer 通过 ``ArticleGenerationService().generate()`` 调用，
不直接调 DeepSeek / 写库，保持与 Celery 路径行为一致。

关于 IntegrityError 竞态（#4）：
    Celery Worker A 与 Kafka Consumer 同时拿到同一用户选择，
    双方同时 INSERT 同一 (topic_id, choice_id) 唯一约束冲突。
    捕获 ``IntegrityError`` → rollback → 重查已有文章 → 返回复用。
    这样无论哪条路径先落库，另一条都会拿到同一 article 对象。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError

from app.core.config import current_datetime, settings
from app.core.database import session_scope
from app.core.pii import hash_openid
from app.models.article import ArticleStatus, GeneratedArticle
from app.models.topic import HotTopic, UserChoice
from app.services.llm_provider import LLMProviderService
from app.services.llm_clients import build_llm_client, LLMClientBase
from app.services.kafka_producer import KafkaProducerService

logger = logging.getLogger(__name__)


class ArticleGenerationError(Exception):
    """文章生成失败时抛出，与业务错误（话题不存在等）返回 dict 区分。"""
    pass


class ArticleGenerationService:
    """统一文章生成服务，替代 Celery Task 与 Kafka Consumer 中的重复逻辑。"""

    def __init__(self, llm_provider_id: int = None) -> None:
        self._provider_id = llm_provider_id
        self._client: Optional[LLMClientBase] = None
        self._producer: Optional[KafkaProducerService] = None

    @property
    def client(self) -> LLMClientBase:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> LLMClientBase:
        """根据 provider_id 获取客户端，未指定则用默认供应商。"""
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            svc = LLMProviderService(db)
            if self._provider_id:
                provider = svc.get_provider(self._provider_id)
                if not provider:
                    raise ValueError(f"LLM Provider {self._provider_id} 不存在")
            else:
                provider = svc.get_default_provider()
                if not provider:
                    raise ValueError("未配置默认 LLM 供应商，请先在管理后台添加并设为默认")
            api_key = svc.decrypt_api_key(provider.id)
            return build_llm_client(
                provider_type=provider.provider_type,
                api_key=api_key or provider.api_key_masked,
                api_url=provider.api_url,
                model=provider.model,
                temperature=provider.temperature,
                timeout=provider.timeout,
                extra_config=provider.extra_config or {},
            )
        finally:
            db.close()

    @property
    def producer(self) -> KafkaProducerService:
        if self._producer is None:
            self._producer = KafkaProducerService()
        return self._producer

    def generate(
        self,
        topic_id: int,
        choice_id: Optional[str] = None,
        user_openid: Optional[str] = None,
        custom_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成文章（主体逻辑）。

        Returns:
            dict with keys:
            - status: "exists" | "success" | "error"
            - article_id: int (when exists or success)
            - title: str (when exists or success)
            - error: str (when status == "error")
        """
        logger.info(
            "ArticleGenerationService.generate: topic_id=%s choice_id=%s user=%s has_custom=%s",
            topic_id,
            choice_id,
            hash_openid(user_openid) if user_openid else None,
            bool(custom_prompt),
        )

        with session_scope() as db:
            topic = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
            if topic is None:
                logger.error("话题不存在: topic_id=%s", topic_id)
                return {"status": "error", "error": f"话题不存在: {topic_id}"}

            # 幂等检查：同一 (topic, choice) 已有文章 → 复用
            if choice_id:
                existing = (
                    db.query(GeneratedArticle)
                    .filter(
                        GeneratedArticle.topic_id == topic.id,
                        GeneratedArticle.choice_id == choice_id,
                    )
                    .first()
                )
                if existing is not None:
                    logger.info(
                        "文章已存在, 跳过重复生成: article_id=%s",
                        existing.id,
                    )
                    db.commit()
                    self._notify_article_generated(
                        article_id=existing.id,
                        topic_id=topic_id,
                        title=existing.title,
                        user_openid=user_openid,
                    )
                    return {
                        "status": "exists",
                        "article_id": existing.id,
                        "title": existing.title,
                    }

            # 调用 LLM 生成
            article_data = self.client.generate_article(
                topic.title, custom_prompt=custom_prompt,
            )
            if not article_data:
                logger.error("文章生成失败: topic_id=%s", topic_id)
                raise ArticleGenerationError(f"文章生成失败: topic_id={topic_id}")

            # cover_description 可能是文字描述而非图片 URL，过滤掉非 URL 内容
            cover_desc = article_data.get("cover_description", "") or ""
            if cover_desc and not cover_desc.startswith(("http://", "https://")):
                cover_desc = ""  # 非 URL 则置空，走微信默认封面

            # 写入数据库
            article = GeneratedArticle(
                topic_id=topic.id,
                choice_id=choice_id or None,
                user_openid=user_openid or None,
                title=article_data.get("title", topic.title),
                content=article_data.get("content", ""),
                summary=(article_data.get("content", "") or "")[:200],
                cover_url=cover_desc,
                status=ArticleStatus.GENERATED,
            )
            db.add(article)
            try:
                db.flush()
            except IntegrityError:
                # 竞态落库：另一条路径（Celery 或 Kafka）已插入同一 (topic, choice)
                db.rollback()
                if choice_id:
                    existing = (
                        db.query(GeneratedArticle)
                        .filter(
                            GeneratedArticle.topic_id == topic.id,
                            GeneratedArticle.choice_id == choice_id,
                        )
                        .first()
                    )
                    if existing is not None:
                        logger.info(
                            "竞态落库, 复用已有文章: article_id=%s",
                            existing.id,
                        )
                        db.commit()
                        self._notify_article_generated(
                            article_id=existing.id,
                            topic_id=topic_id,
                            title=existing.title,
                            user_openid=user_openid,
                        )
                        return {
                            "status": "exists",
                            "article_id": existing.id,
                            "title": existing.title,
                        }
                # 没有 choice_id 或离奇竞态，重新抛出让调用方感知
                raise

            # 标记 UserChoice 已处理
            if choice_id:
                user_choice = (
                    db.query(UserChoice)
                    .filter(UserChoice.choice_id == choice_id)
                    .first()
                )
                if user_choice:
                    user_choice.is_processed = True
                    user_choice.process_time = current_datetime()

            db.commit()
            logger.info("文章生成成功: article_id=%s", article.id)

            # 通知下游（失败仅记录，不影响主流程）
            self._notify_article_generated(
                article_id=article.id,
                topic_id=topic_id,
                title=article.title,
                user_openid=user_openid,
            )

            # 自动发布
            if settings.enable_auto_publish:
                self._trigger_auto_publish(article.id)

            return {
                "status": "success",
                "article_id": article.id,
                "title": article.title,
            }

    # -------------------------------------------------------------------------
    # 内部辅助
    # -------------------------------------------------------------------------

    def _notify_article_generated(
        self,
        article_id: int,
        topic_id: int,
        title: str,
        user_openid: Optional[str] = None,
    ) -> None:
        """发送 article_generated 通知到 Kafka（失败仅记录）。"""
        try:
            self.producer.send_article_generated({
                "article_id": article_id,
                "topic_id": topic_id,
                "title": title,
                "user_openid": user_openid,
            })
        except Exception:  # noqa: BLE001
            logger.error(
                "发送 article_generated 通知失败: article_id=%s", article_id,
            )

    @staticmethod
    def _trigger_auto_publish(article_id: int) -> None:
        """触发自动发布（Celery 任务延迟调用）。"""
        try:
            from app.tasks.article_tasks import publish_article
            publish_article.delay(article_id)
        except Exception:  # noqa: BLE001
            logger.exception("触发 auto_publish 失败: article_id=%s", article_id)
