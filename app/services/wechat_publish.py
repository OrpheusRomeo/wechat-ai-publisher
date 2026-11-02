# app/services/wechat_publish.py
"""微信「建草稿 → 群发」的统一流程。

历史背景
--------
发布逻辑此前散落在三处，行为并不一致：

- ``app/services/article_publishing.py`` → ``ArticlePublishingService.publish``
  （``.env`` 凭据）
- ``app/services/article_publishing.py`` → ``ArticlePublishingService.publish_to_platform``
  （``platform_accounts`` 凭据）
- ``app/tasks/article_tasks.py`` → ``daily_publish_digest``（``.env`` 凭据）

三份拷贝对「配额耗尽 / 48001 无群发权限 / 其它错误」的状态映射各不相同
（一处退化成 DRAFT、一处直接 FAILED），导致同一篇文章从不同入口发布
结果不一致；平台路径还额外缺少配额预检与封面兜底。

本模块把三处共用的部分收敛成一份实现：

- :func:`publish_wechat_draft` —— 建草稿 → 群发 + 状态映射
- :func:`resolve_cover_url` —— 封面解析（http URL / 本地裁剪图）
- :func:`count_published_today` / :func:`check_daily_quota` —— 按账号维度的配额统计
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from app.core.config import today_start
from app.models.article import ArticleStatus, GeneratedArticle
from app.services.wechat_core import QUOTA_ERRCODES

logger = logging.getLogger(__name__)

# 订阅号 / 未认证账号没有群发 (freepublish/submit) 权限
ERRCODE_NO_GROUP_PUBLISH = 48001

_NO_GROUP_PUBLISH_HINT = (
    "草稿已存入微信草稿箱，但订阅号无权自动群发。"
    "请登录微信公众平台 → 内容与互动 → 草稿箱 → 选择该草稿手动群发。"
)


@dataclass
class PublishOutcome:
    """一次「建草稿 → 群发」的结果。"""

    status: str  # "published" | "draft"
    media_id: Optional[str] = None
    publish_id: Optional[str] = None
    errcode: Optional[int] = None
    error_msg: str = ""

    @property
    def published(self) -> bool:
        return self.status == "published"


def publish_wechat_draft(
    wechat: Any,
    *,
    title: str,
    content: str,
    cover_url: Optional[str] = None,
    author: str = "AI编辑部",
    digest: Optional[str] = None,
) -> PublishOutcome:
    """执行「建草稿 → 群发」，返回统一结果。

    Args:
        wechat: 需要提供 ``create_draft(title=, content=, cover_url=, author=,
            digest=)`` 与 ``publish_draft(media_id) -> dict`` 两个方法的对象。
            ``WeChatService``（``.env`` 凭据）与 ``WeChatApiClient``
            （``platform_accounts`` 凭据）都满足该协议，因此两条路径行为一致。

    Returns:
        :class:`PublishOutcome`。``status == "published"`` 表示群发成功；
        其余情况草稿仍留在微信后台，``error_msg`` 说明原因。
    """
    try:
        draft_result = wechat.create_draft(
            title=title,
            content=content,
            cover_url=cover_url,
            author=author,
            digest=digest,
        )
    except Exception:  # noqa: BLE001
        logger.exception("创建微信草稿异常")
        return PublishOutcome(status="draft", error_msg="创建草稿异常")

    if not draft_result or not draft_result.get("media_id"):
        return PublishOutcome(status="draft", error_msg="创建草稿失败")

    media_id = draft_result["media_id"]

    try:
        publish_result = wechat.publish_draft(media_id) or {}
    except Exception:  # noqa: BLE001
        logger.exception("微信群发异常: media_id=%s", media_id)
        return PublishOutcome(
            status="draft", media_id=media_id, errcode=-1,
            error_msg="发布异常, 已保存为草稿",
        )

    errcode = publish_result.get("errcode", -1)
    errmsg = publish_result.get("errmsg", "")

    if errcode == 0:
        return PublishOutcome(
            status="published",
            media_id=media_id,
            publish_id=publish_result.get("publish_id"),
        )

    if errcode == ERRCODE_NO_GROUP_PUBLISH:
        logger.warning(
            "订阅号无权自动群发 errcode=%s, 草稿已入微信后台: media_id=%s",
            errcode, media_id,
        )
        return PublishOutcome(
            status="draft", media_id=media_id, errcode=errcode,
            error_msg=_NO_GROUP_PUBLISH_HINT,
        )

    if errcode in QUOTA_ERRCODES:
        logger.warning("发布触发每日配额 errcode=%s: %s", errcode, publish_result)
        return PublishOutcome(
            status="draft", media_id=media_id, errcode=errcode,
            error_msg=(
                f"发布触发每日配额 errcode={errcode} errmsg={errmsg}, "
                f"已保留为草稿待手动群发"
            ),
        )

    logger.error("发布失败 errcode=%s: %s", errcode, publish_result)
    return PublishOutcome(
        status="draft", media_id=media_id, errcode=errcode,
        error_msg=f"发布失败 errcode={errcode} errmsg={errmsg}, 已保存为草稿",
    )


def resolve_cover_url(
    article: GeneratedArticle,
    db: Any,
    *,
    allow_local: bool = True,
) -> str:
    """解析文章封面。

    1. ``article.cover_url`` 本身是 http(s) URL → 直接使用
    2. 否则从话题 ``source_url`` 抓图并裁剪成 2.35:1 的本地文件
       （仅微信支持本地路径；``allow_local=False`` 时跳过）

    Returns:
        http(s) URL、本地文件路径，或空字符串。
    """
    cover_url = article.cover_url or ""
    if cover_url.startswith(("http://", "https://")):
        return cover_url

    if not allow_local or not article.topic_id:
        return ""

    from app.models.topic import HotTopic

    topic = (
        db.query(HotTopic)
        .filter(HotTopic.id == article.topic_id)
        .first()
    )
    if not topic or not topic.source_url:
        return ""

    from app.utils.image import fetch_and_crop_cover

    local_cover = fetch_and_crop_cover(topic.source_url)
    if not local_cover:
        return ""

    logger.info(
        "封面图已从话题 URL 抓取并裁剪: article_id=%s topic_source=%s",
        article.id, topic.source_url[:80],
    )
    return local_cover


def count_published_today(
    db: Any,
    platform_account_id: Optional[int] = None,
) -> int:
    """统计今日已群发的文章数。

    按发布目标隔离：

    - ``platform_account_id=None`` → ``.env`` 凭据路径发布的文章
      （``platform_account_id IS NULL``）
    - 否则 → 指定平台账号发布的文章

    此前统计的是「今日全部已发布文章」，头条 / 百家号之类的发布也会吃掉
    微信的每日群发配额。
    """
    query = db.query(GeneratedArticle).filter(
        GeneratedArticle.status == ArticleStatus.PUBLISHED,
        GeneratedArticle.publish_time >= today_start(),
    )
    if platform_account_id is None:
        query = query.filter(GeneratedArticle.platform_account_id.is_(None))
    else:
        query = query.filter(
            GeneratedArticle.platform_account_id == platform_account_id,
        )
    return query.count()


def check_daily_quota(
    db: Any,
    quota: int,
    platform_account_id: Optional[int] = None,
) -> Tuple[bool, int]:
    """检查今日群发配额是否还有余额。

    Returns:
        ``(是否还有余额, 今日已发布数)``
    """
    today_published = count_published_today(db, platform_account_id)
    return today_published < quota, today_published
