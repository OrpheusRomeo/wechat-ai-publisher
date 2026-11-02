# app/tasks/crawl_tasks.py
from app.tasks.celery_app import celery_app
from app.services.crawler import BaiduHotCrawler
from app.core.config import settings, today_start
from app.core.database import session_scope
from app.models.topic import HotTopic, TopicStatus
import logging
from app.core.runtime_overrides import apply_runtime_overrides

logger = logging.getLogger(__name__)


def _save_topics(topics: list, platform_account_id: int = None) -> dict:
    """通用的话题保存逻辑（支持指定平台账号）。

    优化: 使用批量查询替代 N+1 查询，先一次性查出所有已存在的 title。
    """
    saved = 0
    updated = 0
    skipped_locked = 0
    today_start_ts = today_start()

    from app.core.database import session_scope
    with session_scope() as db:
        # 收集所有有效标题
        valid_topics = []
        for topic_data in topics:
            title = topic_data.get('title', '').strip()
            if not title:
                continue
            valid_topics.append(topic_data)

        if not valid_topics:
            return {'saved': 0, 'updated': 0, 'skipped_locked': 0}

        # 批量查询：一次性查出今天所有已存在的 title（使用 IN 查询）
        titles = [t.get('title', '').strip() for t in valid_topics]
        query = db.query(HotTopic).filter(
            HotTopic.title.in_(titles),
            HotTopic.create_time >= today_start_ts,
        )
        if platform_account_id:
            query = query.filter(HotTopic.platform_account_id == platform_account_id)
        else:
            query = query.filter(HotTopic.platform_account_id.is_(None))

        existing_topics = {t.title: t for t in query.all()}

        for topic_data in valid_topics:
            title = topic_data.get('title', '').strip()
            heat = topic_data.get('heat_score', 0) or 0
            source_url = topic_data.get('source_url', '') or ''
            new_meta = {
                "img_url": topic_data.get("img_url", "") or "",
                "news_url": topic_data.get("news_url", "") or "",
            }

            existing = existing_topics.get(title)

            if existing:
                if existing.status != TopicStatus.PENDING:
                    skipped_locked += 1
                    continue
                existing.heat_score = max(existing.heat_score or 0, heat)
                if source_url:
                    existing.source_url = source_url
                existing.extra_metadata = {**(existing.extra_metadata or {}), **new_meta}
                updated += 1
                continue

            topic = HotTopic(
                title=title,
                heat_score=heat,
                source_url=source_url,
                status=TopicStatus.PENDING,
                extra_metadata=new_meta,
                platform_account_id=platform_account_id,
            )
            db.add(topic)
            saved += 1
        db.commit()

    return {'saved': saved, 'updated': updated, 'skipped_locked': skipped_locked}


@celery_app.task(bind=True, max_retries=3)
def crawl_hot_topics(self):
    """爬取热点话题（全局版本，不绑定账号）.

    去重策略:
    - 同一天内, 同 title 已存在且 status==PENDING → 就地刷新 heat_score
    - 同一天内, 同 title 已存在但 status != PENDING → 跳过
    - heat_score 取历史最大值
    """
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    logger.info("开始爬取热点（全局）")

    try:
        crawler = BaiduHotCrawler()
        topics = crawler.crawl()

        result = _save_topics(topics, platform_account_id=None)

        logger.info(
            "保存 %d 条新话题, 更新 %d 条已有, 跳过 %d 条已锁定",
            result['saved'], result['updated'], result['skipped_locked'],
        )
        return {
            'status': 'success',
            **result,
            'total': len(topics),
        }

    except Exception as e:
        logger.error("爬取失败: %s", e)
        self.retry(exc=e, countdown=settings.celery_crawl_retry_countdown)


@celery_app.task(bind=True, max_retries=3)
def crawl_for_account(self, account_id: str):
    """为指定账号爬取热点话题.

    根据账号的平台类型选择对应的爬虫。
    """
    # 同步管理后台「系统配置」到运行时（带 TTL 缓存，不会频繁查库）
    apply_runtime_overrides()
    from uuid import UUID, InvalidUUIDError
    from app.models.platform import PlatformAccount
    from app.services.platforms.crawlers import CrawlerFactory

    logger.info("开始为账号 %s 爬取热点", account_id)

    # 验证 UUID 格式，无效 UUID 不重试（永久错误）
    try:
        account_uuid = UUID(account_id)
    except (InvalidUUIDError, ValueError, AttributeError) as exc:
        logger.error("账号 %s UUID 格式无效，不再重试: %s", account_id, exc)
        return {'status': 'failed', 'reason': f'invalid account_id: {account_id}'}

    try:
        with session_scope() as db:
            account = db.query(PlatformAccount).filter(
                PlatformAccount.account_id == account_uuid,
                PlatformAccount.is_deleted == False,
                PlatformAccount.is_active == True,
            ).first()

        if not account:
            logger.warning("账号 %s 未找到或已停用", account_id)
            return {'status': 'skipped', 'reason': 'account not found'}

        # 获取爬虫: 解密 credentials_encrypted (Fernet)
        credentials = {}
        if account.credentials_encrypted:
            try:
                import json
                from cryptography.fernet import Fernet
                fernet = Fernet(settings.effective_fernet_key)
                raw = fernet.decrypt(account.credentials_encrypted.encode()).decode()
                credentials = json.loads(raw)
            except Exception as exc:
                logger.warning(
                    "账号 %s 凭证解密失败, 将以空凭证继续: %s", account_id, exc,
                )

        # 获取爬虫（平台不支持是永久错误，不重试）
        try:
            crawler = CrawlerFactory.get_crawler(account.platform.platform_type, credentials)
        except ValueError as e:
            # 永久错误：平台不支持或配置错误，直接记录并跳过，不重试
            logger.error("账号 %s 爬虫初始化失败（永久错误，不重试）: %s", account_id, e)
            return {"status": "error", "account_id": account_id, "reason": str(e)}

        # 爬取（网络等临时错误可重试）
        try:
            topics = crawler.crawl()
        except Exception as e:
            logger.error("账号 %s 爬虫执行异常: %s", account_id, e)
            self.retry(exc=e, countdown=settings.celery_crawl_retry_countdown)

        # 保存话题（关联到账号）
        result = _save_topics(topics, platform_account_id=account.id)

        logger.info(
            "账号 %s 爬取完成: 保存 %d 条, 更新 %d 条, 跳过 %d 条",
            account_id, result['saved'], result['updated'], result['skipped_locked'],
        )
        return {
            'status': 'success',
            'account_id': account_id,
            'platform_type': account.platform.platform_type,
            **result,
            'total': len(topics),
        }

    except Exception as e:
        logger.error("账号 %s 爬取失败: %s", account_id, e)
        self.retry(exc=e, countdown=settings.celery_crawl_retry_countdown)

