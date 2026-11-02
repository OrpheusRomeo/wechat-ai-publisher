# app/services/article_publishing.py
"""ArticlePublishingService - 统一的文章发布 Service。

从 Celery Task (publish_article) 和 Kafka Consumer (PublishConsumer.process_message)
中提取的共用发布逻辑：

- Redis 分布式锁防止同一 article 并发发布
- SELECT FOR UPDATE 防止锁过期窗口内的并发写入
- 状态早退 (PUBLISHED / PUBLISHING)
- 配额预检 (quota_exceeded 时保存为草稿)
- WeChat 草稿创建 + 群发
- 发布成功后发 Kafka 通知给 push_consumer

Service 为**同步**函数，供 Celery Task / Kafka Consumer 直接调用。
DB session 由调用方通过 ``session_scope()`` 管理。
"""
import logging
from typing import Any, Dict, Optional

from app.core.config import current_datetime, settings
from app.core.database import session_scope
from app.core.pii import hash_openid
from app.core.redis_client import redis_client
from app.models.article import ArticleStatus, GeneratedArticle
from app.services.kafka_producer import KafkaProducerService
from app.services.wechat import WeChatService
from app.services.wechat_publish import (
    check_daily_quota,
    publish_wechat_draft,
    resolve_cover_url,
)

logger = logging.getLogger(__name__)


def _publish_lock_key(article_id: int) -> str:
    """发布锁 key, 与 Celery Task / Kafka Consumer 共用同一把锁."""
    return f"wechat:lock:publish_article:{article_id}"


class ArticlePublishingService:
    """统一的文章发布 Service."""

    def publish(self, article_id: int, publish_type: str = "auto") -> Dict[str, Any]:
        """发布一篇文章到微信公众号。

        Args:
            article_id: 文章 ID
            publish_type: 发布类型, 默认为 "auto"

        Returns:
            包含发布结果的 dict:
            - status: "success" | "skip" | "locked" | "quota_exceeded" | "error"
            - article_id: int
            - publish_status: ArticleStatus 枚举值
            - today_published: int (quota_exceeded 时)
            - quota: int (quota_exceeded 时)
        """
        logger.info("ArticlePublishingService.publish: article_id=%s publish_type=%s",
                     article_id, publish_type)

        lock_key = _publish_lock_key(article_id)
        lock_token = redis_client.acquire_lock_sync(
            lock_key, ttl_seconds=settings.publish_lock_ttl_seconds,
        )
        if not lock_token:
            logger.warning(
                "ArticlePublishingService 锁未取得, 另一 worker 正在处理: article_id=%s",
                article_id,
            )
            return {"status": "locked", "article_id": article_id}

        try:
            with session_scope() as db:
                article = (
                    db.query(GeneratedArticle)
                    .filter(GeneratedArticle.id == article_id)
                    .with_for_update()
                    .first()
                )
                if article is None:
                    raise ValueError(f"文章不存在: {article_id}")

                # 已发布 / 发布中 → 早退
                if article.status in (
                    ArticleStatus.PUBLISHED,
                    ArticleStatus.PUBLISHING,
                ):
                    logger.info(
                        "文章已发布/正在发布, 跳过: article_id=%s status=%s",
                        article.id,
                        article.status,
                    )
                    return {
                        "status": "skip",
                        "article_id": article.id,
                        "publish_status": article.status,
                    }

                # 配额预检（按 .env 凭据路径统计，与平台账号路径互不占用）
                quota = settings.wechat_daily_publish_quota
                quota_ok, today_published = check_daily_quota(db, quota, None)
                if not quota_ok:
                    article.status = ArticleStatus.DRAFT
                    article.error_msg = (
                        f"今日已发布 {today_published}/{quota} 篇, "
                        f"超出每日配额, 已保留为草稿待手动群发"
                    )
                    db.commit()
                    logger.warning(
                        "发布配额耗尽, 跳过 freepublish: article_id=%s "
                        "today_published=%d quota=%d",
                        article.id, today_published, quota,
                    )
                    return {
                        "status": "quota_exceeded",
                        "article_id": article.id,
                        "publish_status": ArticleStatus.DRAFT,
                        "today_published": today_published,
                        "quota": quota,
                    }

                # 标记为发布中
                article.status = ArticleStatus.PUBLISHING
                # 本路径发布到 .env 配的公众号，与平台账号路径的配额统计互斥
                article.platform_account_id = None
                db.flush()

                # 封面图处理：优先用 article.cover_url，否则从话题 source_url 抓取并裁剪
                cover_url = resolve_cover_url(article, db, allow_local=True)

                # 建草稿 → 群发（与平台账号路径共用同一份实现）
                outcome = publish_wechat_draft(
                    WeChatService(),
                    title=article.title,
                    content=article.content,
                    cover_url=cover_url or None,
                )
                article.wechat_media_id = outcome.media_id
                publish_ok = outcome.published

                if publish_ok:
                    article.status = ArticleStatus.PUBLISHED
                    article.publish_time = current_datetime()
                    article.wechat_publish_id = outcome.publish_id
                else:
                    # 草稿仍留在微信后台，退化成 DRAFT 待人工群发
                    article.status = ArticleStatus.DRAFT
                    article.error_msg = outcome.error_msg

                db.commit()

                # 发布成功后发 Kafka 通知
                if publish_ok:
                    self._send_publish_success_notification(article)

                return {
                    "status": "success",
                    "article_id": article.id,
                    "publish_status": article.status,
                }

        except Exception:
            logger.exception("ArticlePublishingService 发布异常: article_id=%s", article_id)
            return {
                "status": "error",
                "article_id": article_id,
                "publish_status": ArticleStatus.DRAFT,
            }
        finally:
            redis_client.release_lock_sync(lock_key, lock_token)

    def _send_publish_success_notification(self, article: GeneratedArticle) -> None:
        """发布成功后向 Kafka 推一条通知, push_consumer 据此发客服消息."""
        if not article.user_openid:
            logger.info(
                "文章无 user_openid, 跳过推送通知: article_id=%s",
                article.id,
            )
            return
        try:
            KafkaProducerService().send_article_generated({
                "article_id": article.id,
                "topic_id": article.topic_id,
                "title": article.title,
                "user_openid": article.user_openid,
                "publish_status": "published",
            })
            logger.info(
                "publish-success 通知已发送: article_id=%s user=%s",
                article.id,
                hash_openid(article.user_openid),
            )
        except Exception:  # noqa: BLE001
            # 通知发送失败不影响发布结果, 仅记录
            logger.exception("发布成功通知发送失败: article_id=%s", article.id)

    # -------------------------------------------------------------------------
    # 多平台发布（头条、小红书等）
    # -------------------------------------------------------------------------

    def _publish_to_wechat_account(
        self,
        *,
        article: GeneratedArticle,
        platform_svc,
        platform_account_id: int,
        db,
    ) -> Dict[str, Any]:
        """把文章发布到微信平台账号。

        与 ``publish()``（``.env`` 凭据）走同一份「建草稿 → 群发」实现，
        差别只在凭据来源与配额统计维度（按账号隔离）。
        """
        quota = settings.wechat_daily_publish_quota
        quota_ok, today_published = check_daily_quota(
            db, quota, platform_account_id,
        )
        if not quota_ok:
            article.status = ArticleStatus.DRAFT
            article.error_msg = (
                f"今日该账号已发布 {today_published}/{quota} 篇, "
                f"超出每日配额, 已保留为草稿待手动群发"
            )
            db.commit()
            logger.warning(
                "微信平台账号发布配额耗尽: article_id=%s account=%s "
                "today_published=%d quota=%d",
                article.id, platform_account_id, today_published, quota,
            )
            return {
                "status": "quota_exceeded",
                "article_id": article.id,
                "publish_status": ArticleStatus.DRAFT,
                "today_published": today_published,
                "quota": quota,
                "platform_type": "wechat",
            }

        article.status = ArticleStatus.PUBLISHING
        db.flush()

        # 封面兜底：article.cover_url 非 http(s) 时从话题 source_url 抓图裁剪
        cover_url = resolve_cover_url(article, db, allow_local=True)

        outcome = publish_wechat_draft(
            platform_svc.api_client,
            title=article.title,
            content=article.content,
            cover_url=cover_url or None,
        )

        article.platform_account_id = platform_account_id
        article.wechat_media_id = outcome.media_id

        if outcome.published:
            article.platform_media_id = outcome.media_id
            article.status = ArticleStatus.PUBLISHED
            article.publish_time = current_datetime()
            article.wechat_publish_id = outcome.publish_id
            db.commit()
            logger.info(
                "微信平台账号发布成功: article_id=%s account=%s media_id=%s",
                article.id, platform_account_id, outcome.media_id,
            )
            self._send_publish_success_notification(article)
            return {
                "status": "success",
                "article_id": article.id,
                "platform_media_id": outcome.media_id,
                "platform_type": "wechat",
            }

        # 草稿已留在微信后台 → DRAFT 待人工群发
        article.status = ArticleStatus.DRAFT
        article.error_msg = outcome.error_msg
        db.commit()
        logger.warning(
            "微信平台账号发布未成功(草稿已保留): article_id=%s account=%s "
            "errcode=%s errmsg=%s",
            article.id, platform_account_id, outcome.errcode, outcome.error_msg,
        )
        return {
            "status": "error",
            "article_id": article.id,
            "errcode": outcome.errcode,
            "errmsg": outcome.error_msg,
            "platform_type": "wechat",
        }

    def publish_to_platform(
        self,
        article_id: int,
        platform_account_id: int,
    ) -> Dict[str, Any]:
        """将文章发布到指定平台账号（头条、小红书等）。

        通过 platform_account_id 加载 PlatformAccount，加载对应 PlatformService，
        调用其 publish_article()，成功后回写 platform_media_id。

        Args:
            article_id: 文章 ID
            platform_account_id: 平台账号 ID

        Returns:
            包含发布结果的 dict:
            - status: "success" | "locked" | "error"
            - article_id: int
            - platform_media_id: str (成功时)
            - url: str (头条成功时返回文章预览 URL)
            - errcode / errmsg: 失败时
        """
        from app.models.platform import PlatformAccount
        from app.services.platforms import PlatformServiceFactory

        logger.info(
            "publish_to_platform: article_id=%s platform_account_id=%s",
            article_id, platform_account_id,
        )

        lock_key = f"platform:lock:publish:{article_id}:{platform_account_id}"
        lock_token = redis_client.acquire_lock_sync(lock_key, ttl_seconds=120)
        if not lock_token:
            logger.warning(
                "publish_to_platform 锁未取得: article_id=%s platform_account_id=%s",
                article_id, platform_account_id,
            )
            return {"status": "locked", "article_id": article_id}

        try:
            with session_scope() as db:
                article = (
                    db.query(GeneratedArticle)
                    .filter(GeneratedArticle.id == article_id)
                    .with_for_update()
                    .first()
                )
                if article is None:
                    raise ValueError(f"文章不存在: {article_id}")

                # 加载平台账号
                account: Optional[PlatformAccount] = (
                    db.query(PlatformAccount)
                    .filter(
                        PlatformAccount.id == platform_account_id,
                        PlatformAccount.is_deleted.is_(False),
                        PlatformAccount.is_active.is_(True),
                    )
                    .first()
                )
                if not account:
                    raise ValueError(f"平台账号不存在或已禁用: {platform_account_id}")

                platform_type = account.platform.platform_type if account.platform else ""
                logger.info(
                    "开始发布到平台: article_id=%s platform=%s account=%s",
                    article_id, platform_type, account.account_name,
                )

                # 路由到对应平台服务
                try:
                    platform_svc = PlatformServiceFactory.get_service(account)
                except ValueError:
                    logger.error("不支持的平台类型: %s", platform_type)
                    return {
                        "status": "error",
                        "article_id": article_id,
                        "errmsg": f"不支持的平台: {platform_type}",
                    }
                if platform_svc is None:
                    logger.error("平台账号缺少平台定义: account=%s", platform_account_id)
                    return {
                        "status": "error",
                        "article_id": article_id,
                        "errmsg": "平台账号缺少平台定义",
                    }

                # 微信走与 .env 凭据路径完全一致的发布流程
                # (配额预检 / 封面兜底 / 48001 → DRAFT / 成功通知)
                if platform_type == "wechat":
                    return self._publish_to_wechat_account(
                        article=article,
                        platform_svc=platform_svc,
                        platform_account_id=platform_account_id,
                        db=db,
                    )

                # 封面处理：非 HTTP URL 则置空
                cover_url = article.cover_url or ""
                if cover_url and not cover_url.startswith(("http://", "https://")):
                    cover_url = ""

                # 调用平台发布
                result = platform_svc.publish_article(
                    title=article.title,
                    content=article.content,
                    cover_url=cover_url or None,
                )

                if result.success:
                    article.platform_account_id = platform_account_id
                    article.platform_media_id = result.media_id
                    article.status = ArticleStatus.PUBLISHED
                    article.publish_time = current_datetime()
                    db.commit()
                    logger.info(
                        "平台发布成功: article_id=%s platform=%s media_id=%s",
                        article_id, platform_type, result.media_id,
                    )
                    return {
                        "status": "success",
                        "article_id": article_id,
                        "platform_media_id": result.media_id,
                        "url": result.url or "",
                        "platform_type": platform_type,
                    }
                else:
                    # 发布失败，记录 error_msg
                    article.status = ArticleStatus.FAILED
                    article.error_msg = (
                        f"[{platform_type}] 发布失败: "
                        f"errcode={result.errcode} errmsg={result.errmsg}"
                    )
                    db.commit()
                    logger.warning(
                        "平台发布失败: article_id=%s platform=%s errcode=%s errmsg=%s",
                        article_id, platform_type, result.errcode, result.errmsg,
                    )
                    return {
                        "status": "error",
                        "article_id": article_id,
                        "errcode": result.errcode,
                        "errmsg": result.errmsg,
                        "platform_type": platform_type,
                    }

        except ValueError as exc:
            logger.error("publish_to_platform 业务错误: %s", exc)
            return {
                "status": "error",
                "article_id": article_id,
                "errmsg": str(exc),
            }
        except Exception:
            logger.exception("publish_to_platform 异常: article_id=%s", article_id)
            return {
                "status": "error",
                "article_id": article_id,
                "errmsg": "发布异常",
            }
        finally:
            redis_client.release_lock_sync(lock_key, lock_token)
