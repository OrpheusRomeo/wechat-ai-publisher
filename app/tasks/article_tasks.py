# app/tasks/article_tasks.py
"""Celery 任务：生成 / 发布文章、处理用户选择。

generate_article 和 publish_article 委托给 ArticleGenerationService /
ArticlePublishingService 实现，Celery Task 只保留 retry wrapper。

保留任务：daily_publish_digest、process_user_choices、generate_deep_article。
"""
import logging
from datetime import datetime

from app.core.config import current_datetime, settings, today_start
from app.core.database import session_scope
from app.core.pii import hash_openid  # #10
from app.models.article import ArticleStatus, GeneratedArticle
from app.models.topic import HotTopic, UserChoice
from app.services.daily_digest import DailyDigestService
from app.services.kafka_producer import KafkaProducerService
from app.services.wechat import WeChatService, _QUOTA_ERRCODES
from app.services.wechat_publish import count_published_today, publish_wechat_draft
from app.tasks.celery_app import celery_app
from app.core.runtime_overrides import apply_runtime_overrides

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def generate_article(
    self,
    topic_id: int,
    choice_id: str = "",
    user_openid: str = "",
    custom_prompt: str = "",
    llm_provider_id: int = None,
):
    """生成文章。委托给 ArticleGenerationService.generate()"""
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    from app.services.article_generation import ArticleGenerationService
    logger.info(
        "生成文章: topic_id=%s choice_id=%s user=%s has_custom=%s provider=%s",
        topic_id,
        choice_id,
        hash_openid(user_openid) if user_openid else None,
        bool(custom_prompt),
        llm_provider_id,
    )
    try:
        svc = ArticleGenerationService(llm_provider_id=llm_provider_id)
        result = svc.generate(
            topic_id=topic_id,
            choice_id=choice_id or None,
            user_openid=user_openid or None,
            custom_prompt=custom_prompt or None,
        )
        return result
    except Exception as exc:
        logger.error("生成失败: %s", exc)
        self.retry(exc=exc, countdown=settings.celery_generate_retry_countdown)


@celery_app.task(bind=True, max_retries=3)
def publish_article(self, article_id: int):
    """发布文章。委托给 ArticlePublishingService.publish()"""
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    from app.services.article_publishing import ArticlePublishingService
    logger.info("发布文章: article_id=%s", article_id)
    try:
        svc = ArticlePublishingService()
        return svc.publish(article_id=article_id)
    except Exception as exc:
        logger.error("发布失败: %s", exc)
        self.retry(exc=exc, countdown=settings.celery_publish_retry_countdown)


# =============================================================================
# 关键词深度文章生成 (管理后台手动触发)
# =============================================================================

@celery_app.task(bind=True, max_retries=3)
def generate_deep_article(
    self,
    keyword: str,
    custom_prompt: str = "",
    style: str = "standard",
    auto_publish: bool = False,
    llm_provider_id: int = None,
):
    """根据关键词生成深度分析文章（管理后台手动触发）。

    幂等：SHA256(keyword + today_date) 存入 extra_metadata["keyword_hash"]
    避免同一天对同一关键词重复生成。
    """
    import hashlib

    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()

    today_str = today_start().strftime("%Y-%m-%d")
    keyword_hash = hashlib.sha256(f"{keyword}:{today_str}".encode()).hexdigest()[:12]

    logger.info(
        "关键词文章生成: keyword=%s hash=%s style=%s auto_publish=%s provider=%s",
        keyword, keyword_hash, style, auto_publish, llm_provider_id,
    )

    try:
        with session_scope() as db:
            # 获取 LLM 客户端
            from app.services.llm_provider import LLMProviderService
            from app.services.llm_clients import build_llm_client
            svc = LLMProviderService(db)
            if llm_provider_id:
                provider = svc.get_provider(llm_provider_id)
                if not provider:
                    raise ValueError(f"LLM Provider {llm_provider_id} 不存在")
            else:
                provider = svc.get_default_provider()
                if not provider:
                    raise ValueError("未配置默认 LLM 供应商")
            api_key = svc.decrypt_api_key(provider.id)
            llm_client = build_llm_client(
                provider_type=provider.provider_type,
                api_key=api_key or provider.api_key_masked,
                api_url=provider.api_url,
                model=provider.model,
                temperature=provider.temperature,
                timeout=provider.timeout,
                extra_config=provider.extra_config or {},
            )

            # 幂等检查
            existing = (
                db.query(GeneratedArticle)
                .filter(
                    GeneratedArticle.extra_metadata["keyword_hash"].astext == keyword_hash,
                )
                .first()
            )
            if existing is not None:
                logger.info(
                    "关键词文章已存在, 跳过: article_id=%s keyword=%s",
                    existing.id, keyword,
                )
                return {
                    "status": "exists",
                    "article_id": existing.id,
                    "title": existing.title,
                }

            # 调用 LLM 生成
            article_data = llm_client.generate_article_by_keyword(
                keyword=keyword,
                custom_prompt=custom_prompt or None,
                style=style,
            )
            if not article_data:
                raise ValueError("关键词文章生成失败")

            article = GeneratedArticle(
                topic_id=None,  # 关键词文章不关联话题
                choice_id=None,
                user_openid=None,
                title=article_data.get("title", keyword),
                content=article_data.get("content", ""),
                summary=(article_data.get("content", "") or "")[:200],
                cover_url=(lambda c: c if c.startswith(("http://", "https://")) else "")(article_data.get("cover_description", "") or ""),
                status=ArticleStatus.GENERATED,
                extra_metadata={
                    "keyword": keyword,
                    "keyword_hash": keyword_hash,
                    "style": style,
                    "source": "manual_keyword",
                    "generated_date": today_str,
                },
            )
            db.add(article)
            db.flush()

            logger.info("关键词文章生成成功: id=%d title=%s", article.id, article.title)

            db.commit()

            # 自动发布到草稿箱
            if auto_publish:
                publish_article.delay(article.id)

            return {
                "status": "success",
                "article_id": article.id,
                "title": article.title,
            }
    except Exception as exc:  # noqa: BLE001
        logger.error("关键词文章生成失败 keyword=%s: %s", keyword, exc)
        self.retry(exc=exc, countdown=settings.celery_generate_retry_countdown)


# -------------------------------------------------------------------------- #

@celery_app.task
def process_user_choices():
    """每 30 分钟调度一次: 把未处理的 UserChoice 转成 Kafka 生成请求.

    修复 L7: limit 从 10 提到 200, 防积压追不上.
    切换到 Kafka: 不再 ``generate_article.delay()``, 而是
    ``KafkaProducerService().send_generate_request(...)`` 投到
    ``kafka_topic_generate`` 由 ``generate_consumer`` 异步消费.
    """
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    from confluent_kafka import KafkaException

    logger.info("处理用户选择")
    try:
        with session_scope() as db:
            choices = db.query(UserChoice).filter(
                UserChoice.is_processed.is_(False)
            ).limit(200).all()
            producer = KafkaProducerService()
            produced = 0
            for choice in choices:
                producer.send_generate_request({
                    "topic_id": choice.topic_id,
                    "choice_id": str(choice.choice_id),
                    "user_openid": choice.user_openid or "",
                })
                produced += 1
            # flush 确保 broker 收到再 commit, 避免重启丢失任务
            # flush 失败时抛出异常，不 commit DB
            try:
                producer.flush(timeout=5.0)
            except KafkaException as exc:
                logger.error("Kafka flush 失败: %s, 消息未确认送达", exc)
                raise  # 不 commit，让任务重试
            db.commit()
            logger.info("投递 %d 个生成请求到 kafka_topic_generate", produced)
            return {"processed": produced}
    except KafkaException:
        raise  # 已记录，直接向上抛出
    except Exception as exc:  # noqa: BLE001
        logger.error("处理失败: %s", exc)


# =============================================================================
# D 方案: 每日话题日报群发
# =============================================================================
#
# 订阅号没有客服消息权限, 09:00 客服消息推送对绝大多数沉睡用户 0 触达.
# 唯一能覆盖所有已关注用户的渠道是群发 (``freepublish/submit``) ——
# 受 ``WECHAT_DAILY_PUBLISH_QUOTA`` 限制 (个人订阅号 1 篇/天).
# 把这个配额用于"今日话题日报": 一篇覆盖所有粉丝, 比对每个用户
# 发客服消息更现实.
#
# 任务名: ``app.tasks.article_tasks.daily_publish_digest``
# 默认调度: 09:30 (在 push 任务之后), 见 ``celery_app._build_beat_schedule``
# 幂等键: GeneratedArticle.extra_metadata.digest_date = "YYYY-MM-DD"
#         同一日重复触发时不会创建第二篇
# 配额耗尽: 退化为 DRAFT, ``error_msg`` 写明原因, 不阻塞后续日期

# _DIGEST_TOPIC_LIMIT 已移除 → 直接使用 settings.scheduler_topic_limit
# _QUOTA_ERRCODES 已移除 → 直接引用 app.services.wechat._QUOTA_ERRCODES


@celery_app.task(bind=True, max_retries=2)
def daily_publish_digest(self):
    """每天群发一篇"今日话题日报", 让所有已关注用户看到.

    流程:
    1. 取今日 (DB 服务器日期) top N 话题; 0 条时直接 skip + log
    2. 幂等检查: ``extra_metadata.digest_date == today`` 已存在 → skip
    3. 组装 markdown via ``DailyDigestService.build_digest``
    4. 写 GeneratedArticle (status=PUBLISHING, choice_id=NULL, topic_id=top1)
    5. ``wechat.create_draft`` + ``wechat.publish_draft``
    6. 成功: status=PUBLISHED, publish_time=now
       配额耗尽: status=DRAFT + error_msg 注明 errcode
       其它失败: status=DRAFT + error_msg 注明
    """
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    today_str = today_start().strftime("%Y-%m-%d")
    logger.info("daily_publish_digest 启动: date=%s", today_str)

    try:
        with session_scope() as db:
            # 1) 幂等检查: 今日是否已经发过一篇
            existing = (
                db.query(GeneratedArticle)
                .filter(
                    GeneratedArticle.extra_metadata["digest_date"].astext == today_str,
                )
                .first()
            )
            if existing is not None:
                logger.info(
                    "今日已存在 digest 文章, 跳过: article_id=%s status=%s",
                    existing.id, existing.status,
                )
                return {
                    "status": "skip",
                    "reason": "already_published_today",
                    "article_id": existing.id,
                }

            # 2) 拉今日 top 话题
            today_start_ts = today_start()
            topics = (
                db.query(HotTopic)
                .filter(HotTopic.create_time >= today_start_ts)
                .order_by(HotTopic.create_time.desc(), HotTopic.heat_score.desc())
                .limit(settings.scheduler_topic_limit)
                .all()
            )
            if not topics:
                logger.info("今日无话题, 跳过 digest 发布")
                return {"status": "skip", "reason": "no_topics_today"}

            # 3) 调默认 LLM 给每条 topic 生成 30-40 字简讯 (失败回退原标题)
            from app.services.llm_provider import LLMProviderService
            from app.services.llm_clients import build_llm_client
            llm_svc = LLMProviderService(db)
            provider = llm_svc.get_default_provider()
            llm_client = None
            if provider:
                api_key = llm_svc.decrypt_api_key(provider.id)
                llm_client = build_llm_client(
                    provider_type=provider.provider_type,
                    api_key=api_key or provider.api_key_masked,
                    api_url=provider.api_url,
                    model=provider.model,
                    temperature=provider.temperature,
                    timeout=provider.timeout,
                    extra_config=provider.extra_config or {},
                )

            briefs: dict[int, str] = {}
            for t in topics:
                brief = ""
                if llm_client:
                    brief = llm_client.generate_brief(
                        f"请基于话题《{t.title}》写一段中文新闻简讯，"
                        "30-40 字左右，先总评事件核心，再点出意义或悬念。\n"
                        "要求：客观、连贯，不要带话题标签、不要带引号、不要列表符号，"
                        "直接给一段完整句子即可，不要任何解释说明。"
                    )
                if brief:
                    briefs[t.id] = brief
                # 失败时 build_digest 会用原标题兜底, 这里什么也不做
            logger.info("digest 简讯: %d/%d 条生成成功", len(briefs), len(topics))

            # 4) 组装文案 + 取第一条话题配图做封面
            svc = DailyDigestService()
            title, content_md, summary, topic_count, cover_img_url = svc.build_digest(
                topics,
                briefs=briefs,
                app_name=settings.app_name,
                digest_date=today_start().date(),
            )

            # 5) 配额预检 (与 publish_article 同一逻辑)
            quota = settings.wechat_daily_publish_quota
            today_published = count_published_today(db, None)
            # digest 走"占满"策略: 如果今日还剩额度, 直接发; 没有就退 DRAFT
            quota_left = max(0, quota - today_published)
            if quota_left == 0:
                logger.warning(
                    "digest 配额耗尽 (today_published=%d quota=%d), "
                    "留 DRAFT 让人工群发",
                    today_published, quota,
                )
                digest_article = GeneratedArticle(
                    topic_id=topics[0].id,
                    choice_id=None,
                    user_openid=None,
                    title=title,
                    content=content_md,
                    summary=summary,
                    status=ArticleStatus.DRAFT,
                    error_msg=(
                        f"今日已发布 {today_published}/{quota} 篇, "
                        f"digest 占满配额, 需手动群发"
                    ),
                    extra_metadata={
                        "is_digest": True,
                        "digest_date": today_str,
                        "topic_count": topic_count,
                        "cover_img_url": cover_img_url,
                    },
                )
                db.add(digest_article)
                db.commit()
                return {
                    "status": "quota_exceeded",
                    "article_id": digest_article.id,
                    "today_published": today_published,
                    "quota": quota,
                }

            # 6) 写 PUBLISHING 行
            digest_article = GeneratedArticle(
                topic_id=topics[0].id,
                choice_id=None,
                user_openid=None,
                title=title,
                content=content_md,
                summary=summary,
                cover_url=cover_img_url or None,
                status=ArticleStatus.PUBLISHING,
                extra_metadata={
                    "is_digest": True,
                    "digest_date": today_str,
                    "topic_count": topic_count,
                    "cover_img_url": cover_img_url,
                },
            )
            db.add(digest_article)
            db.flush()  # 拿到 id

            # 7) 调微信 (封面图用第一条话题配图, 失败时 wechat 自动回退默认图)
            outcome = publish_wechat_draft(
                WeChatService(),
                title=title,
                content=content_md,
                cover_url=cover_img_url or None,
                author=settings.app_name,
                digest=summary,
            )
            digest_article.wechat_media_id = outcome.media_id

            if outcome.published:
                digest_article.status = ArticleStatus.PUBLISHED
                digest_article.publish_time = current_datetime()
                digest_article.wechat_publish_id = outcome.publish_id
                db.commit()
                logger.info(
                    "digest 发布成功: article_id=%d publish_id=%s topics=%d",
                    digest_article.id,
                    digest_article.wechat_publish_id,
                    topic_count,
                )
                # 不发 push 通知, 群发本身已触达所有用户
                return {
                    "status": "published",
                    "article_id": digest_article.id,
                    "topic_count": topic_count,
                }

            # 创建草稿本身失败 vs 群发失败，两种都退化成 DRAFT
            digest_article.status = ArticleStatus.DRAFT
            digest_article.error_msg = outcome.error_msg
            db.commit()

            if not outcome.media_id:
                logger.error(
                    "digest 创建草稿失败: article_id=%d", digest_article.id,
                )
                return {"status": "draft_failed", "article_id": digest_article.id}

            # 配额类错误
            if outcome.errcode in _QUOTA_ERRCODES:
                logger.warning(
                    "digest 触发配额: errcode=%s article_id=%d",
                    outcome.errcode, digest_article.id,
                )
                return {
                    "status": "quota_exceeded",
                    "errcode": outcome.errcode,
                    "article_id": digest_article.id,
                }

            # 其它错误 (token 失效/网络/未知) → DRAFT + 不重试
            logger.error(
                "digest 发布失败: errcode=%s article_id=%d msg=%s",
                outcome.errcode, digest_article.id, outcome.error_msg,
            )
            return {
                "status": "draft_kept",
                "errcode": outcome.errcode,
                "article_id": digest_article.id,
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily_publish_digest 异常: %s", exc)
        # 退避后重试, 上限 2 次 (max_retries=2)
        # 与 publish_article 保持一致: self.retry() 内部会 raise Retry,
        # 不要再加 raise 关键字
        self.retry(exc=exc, countdown=settings.celery_publish_retry_countdown)
        raise
