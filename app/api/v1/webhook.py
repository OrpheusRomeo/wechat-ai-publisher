# app/api/v1/webhook.py
"""微信公众平台事件回调入口。

- GET /webhook/wechat : URL 校验
- POST /webhook/wechat : 消息/事件回调
    - ``event=subscribe`` : 写入 ``user_subscriptions`` + 被动回复欢迎语
      (含今日 top 10 话题, 走被动回复 API, 不需要客服消息 48h 窗口)
    - ``event=CLICK`` / ``event=VIEW`` : 解析 ``EventKey=CHOOSE_TOPIC_<id>``
      写入 ``user_choices`` + 把话题置为 ``SELECTED``
"""
import hashlib
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from xml.etree import ElementTree as ET

from app.api.deps import get_db
from app.core.config import settings, today_start
from app.core.database import Base  # noqa: F401  确保 model 已注册
from app.core.pii import hash_openid, truncate_content  # #10 PII 脱敏
from app.core.redis_client import redis_client
from app.models.topic import HotTopic, TopicStatus, UserChoice
from app.models.user import UserSubscription

logger = logging.getLogger(__name__)
router = APIRouter()

# 约定的 EventKey 前缀: 自定义菜单 click 事件发 "CHOOSE_TOPIC_<id>"
# 例如 EventKey="CHOOSE_TOPIC_42" 表示用户选择了 topic_id=42
_CHOOSE_TOPIC_RE = re.compile(r"^CHOOSE_TOPIC_(\d+)$")

# 关注时被动回复的欢迎语: 微信被动回复 text 限制 2048 字节,
# 我们保守限制在 1500 字符, 给中英文混排 + emoji 留余量.
_WELCOME_MAX_CHARS = 1500
# 欢迎语里列出的 top 话题数: 默认沿用 scheduler_topic_limit (与每日推送一致)
# 单独 5/10 都可以, 这里给个 10 的硬上限, 避免话题标题过长撑爆文案
_WELCOME_TOPIC_LIMIT = 10


def _touch_interact(db: Session, openid: str) -> None:
    """把 user_subscriptions.last_interact_time 刷到 now。

    订阅号客服消息只能发给 48h 内互动过的用户 (errcode=45015);
    关注 / 点菜单 / 发文本消息都应该视为一次互动, 否则 48h 窗口判断
    永远基于 subscribe_time, 错失大量推送机会。
    """
    if not openid:
        return
    db.query(UserSubscription).filter(
        UserSubscription.openid == openid,
    ).update({"last_interact_time": func.now()})


def _verify_signature(signature: str, timestamp: str, nonce: str, token: str) -> bool:
    """按微信公众号文档校验签名：sha1(sort([token, timestamp, nonce]))。"""
    items = sorted([token, timestamp, nonce])
    digest = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
    return digest == signature


def _get_today_top_topics(
    db: Session, limit: int = _WELCOME_TOPIC_LIMIT,
) -> List[HotTopic]:
    """取用于欢迎语的 top 话题。

    主路径: 今日 (配置时区) 的话题按热度倒序取 top N.
    降级路径: 今日 0 条 (例如 08:00 爬虫还没跑) 时, 退到"最近一次爬到的" top N,
    避免给新用户推一条空欢迎语, 体验更连贯.
    """
    today_topics = (
        db.query(HotTopic)
        .filter(HotTopic.create_time >= today_start())
        .order_by(HotTopic.create_time.desc(), HotTopic.heat_score.desc())
        .limit(limit)
        .all()
    )
    if today_topics:
        return today_topics
    # 降级: 不带日期过滤, 拿最近一次爬到的 top
    return (
        db.query(HotTopic)
        .order_by(HotTopic.create_time.desc(), HotTopic.heat_score.desc())
        .limit(limit)
        .all()
    )


def _build_welcome_message(
    topics: List[HotTopic], app_name: str = "AI 早报",
) -> str:
    """把 top 话题列表拼成欢迎消息文本。

    - 0 条话题: 返回降级文案, 仍欢迎用户
    - >0 条: 欢迎语 + 编号列表 (1. 标题 🔥热度)
    - 截断到 _WELCOME_MAX_CHARS 字符, 避免微信被动回复 2048 字节限制溢出

    这个函数是纯函数 (无 DB / 无 IO), 方便单元测试.
    """
    header = f"🎉 欢迎关注 {app_name}！\n"
    if not topics:
        body = (
            "\n今日暂无话题数据, 请稍后再来看看 👀\n\n"
            f"每天 {settings.scheduler_crawl_time} 准时更新热门话题, "
            "回复\"历史\"可查看已发布文章。"
        )
        text = header + body
    else:
        lines = [f"\n📰 今日热门话题 TOP {len(topics)}：\n"]
        for idx, topic in enumerate(topics, start=1):
            # 标题里可能有换行, 替换成空格避免破坏列表格式
            title = (topic.title or "").replace("\n", " ").strip()
            # 单条标题也加个软上限, 避免某个长标题单独撑爆文案
            if len(title) > 60:
                title = title[:57] + "..."
            heat = int(getattr(topic, "heat_score", 0) or 0)
            lines.append(f"{idx}. {title}  🔥{heat}")
        lines.append(
            "\n回复数字或点击菜单\"今日话题\"选择感兴趣的话题, "
            "AI 将为您生成专属文章 📝"
        )
        text = header + "\n".join(lines)

    if len(text) > _WELCOME_MAX_CHARS:
        text = text[: _WELCOME_MAX_CHARS - 3] + "..."
    return text


@router.get("/wechat")
async def verify_wechat(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """微信公众号接入校验。修复 L9: echostr 必须原样字符串返回, 不要 int()。

    新增: timestamp freshness 检查 + nonce 防重放保护 (5 分钟窗口)。
    """
    import time

    # 1. timestamp 超过 5 分钟视为无效请求 (重放攻击防护)
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid timestamp")
    if abs(time.time() - ts) > 300:
        logger.warning("微信 URL 校验 timestamp 过期: ts=%s", timestamp)
        raise HTTPException(status_code=403, detail="timestamp expired")

    # 2. nonce 防重放: 同一 nonce 在 5 分钟内不能重复使用
    # 使用 Redis SET NX EX 原子操作，key 不存在才设置成功
    nonce_key = f"wechat:nonce:{nonce}"
    is_new = await redis_client.set_nx(nonce_key, "1", expire=300)
    if is_new is None or is_new is False:
        # key 已存在 = 同一 nonce 已被使用过 = 重放攻击
        logger.warning("微信 URL 校验 nonce 重放: nonce=%s", nonce)
        raise HTTPException(status_code=403, detail="nonce reused")

    if _verify_signature(signature, timestamp, nonce, settings.wechat_token):
        logger.info("微信 URL 校验通过")
        # 微信文档: echostr 必须原样字符串返回 (含非数字场景)
        return echostr
    raise HTTPException(status_code=403, detail="invalid signature")


@router.post("/wechat")
async def receive_wechat_message(request: Request, db: Session = Depends(get_db)):
    """接收并解析微信推送的 XML。

    - 文本消息: 回显 (兼容旧行为)
    - event=subscribe: upsert ``user_subscriptions``
    - event=unsubscribe: 标记 ``is_active=False``
    - event=CLICK (自定义菜单): 解析 ``EventKey=CHOOSE_TOPIC_<id>`` → 写
      ``user_choices`` + 把话题置为 ``SELECTED``
    - event=VIEW (网页跳转) / 其它 event: 仅日志, 不回复
    """
    raw = await request.body()
    try:
        # 防御 XXE: 微信回调不包含 DOCTYPE, 若包含则拒绝解析
        # 注: Python xml.etree.ElementTree 从 3.3.3/3.4 起默认不处理外部实体,
        # 此检查为纵深防御, 即使 ET 默认安全也不移除以防万一
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            logger.warning("XXE 威胁检测到, 拒绝解析")
            raise HTTPException(status_code=400, detail="invalid xml")
        root = ET.fromstring(raw)
    except ET.ParseError:
        logger.warning("微信回调 XML 解析失败: %r", raw[:200])
        raise HTTPException(status_code=400, detail="invalid xml")
    except UnicodeDecodeError:
        logger.warning("微信回调 body 解码失败，不是有效 UTF-8")
        raise HTTPException(status_code=400, detail="invalid encoding")

    msg_type = root.findtext("MsgType", default="")
    from_user = root.findtext("FromUserName", default="")
    to_user = root.findtext("ToUserName", default="")

    if msg_type.lower() == "event":
        event = (root.findtext("Event", default="") or "").lower()
        event_key = root.findtext("EventKey", default="") or ""
        logger.info(
            "微信事件回调: event=%s event_key=%s from=%s",
            event,
            event_key,
            hash_openid(from_user),
        )

        if event == "subscribe":
            # 关注时被动回复欢迎语 (含今日 top 话题).
            # 走被动回复 API, 不需要客服消息 48h 窗口, 订阅号也能用.
            welcome_text = _handle_subscribe(db, from_user)
            if welcome_text:
                return _build_text_reply(to_user, from_user, welcome_text)
        elif event == "unsubscribe":
            _handle_unsubscribe(db, from_user)
        elif event == "click":
            _handle_click_event(db, from_user, event_key)
        # 其它 event (scan / location / view / ...) 一律不回 text
        return ""

    content = root.findtext("Content", default="")

    # #10 PII 脱敏: openid 用散列, 内容用截断, 不在 INFO 日志里留原始 PII
    logger.info(
        "收到微信消息 type=%s from=%s bytes=%d content=%s",
        msg_type,
        hash_openid(from_user),
        len(content),
        truncate_content(content, max_chars=30),
    )
    # 用户发消息也是互动, 刷新 last_interact_time
    _touch_interact(db, from_user)
    return _build_text_reply(to_user, from_user, f"已收到您的消息：{content}")


# ----------------- 业务 handler -----------------

def _handle_subscribe(db: Session, openid: str) -> Optional[str]:
    """关注事件: upsert user_subscriptions, 让后续推送有目标用户.

    顺便准备欢迎语 (含今日 top 话题) 给 endpoint 做被动回复.
    关注 upsert 是主路径, 必须成功; 欢迎语查询失败也不能阻塞订阅本身,
    所以 SQL 异常时返回 None, 让用户至少订阅成功.
    """
    if not openid:
        return None
    existing = (
        db.query(UserSubscription).filter(UserSubscription.openid == openid).first()
    )
    if existing is None:
        db.add(UserSubscription(openid=openid, is_active=True))
        logger.info("新用户订阅: %s", hash_openid(openid))
    else:
        existing.is_active = True
        logger.info("用户重新订阅: %s", hash_openid(openid))
    db.commit()
    # 关注本身是互动, 刷新 last_interact_time 让 48h 窗口生效
    _touch_interact(db, openid)

    # 准备欢迎语. 用 try 包住, 任何 DB/异常都不应该阻塞订阅 upsert.
    try:
        topics = _get_today_top_topics(db)
        return _build_welcome_message(topics, app_name=settings.app_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "构造欢迎语失败 (订阅已成功): openid=%s err=%r",
            hash_openid(openid), exc,
        )
        return None


def _handle_unsubscribe(db: Session, openid: str) -> None:
    """取消关注: 标记 is_active=False, 不删除记录 (历史推送可追溯)."""
    if not openid:
        return
    sub = (
        db.query(UserSubscription).filter(UserSubscription.openid == openid).first()
    )
    if sub is not None:
        sub.is_active = False
        db.commit()
        logger.info("用户取消订阅: %s", hash_openid(openid))


def _handle_click_event(db: Session, openid: str, event_key: str) -> None:
    """自定义菜单 click: EventKey 约定 ``CHOOSE_TOPIC_<id>`` → 写 user_choices.

    与 ``POST /api/v1/topics/choice`` 行为一致, 命中 ``uq_user_choice_openid_topic``
    唯一约束时静默忽略 (用户重复点菜单).
    """
    if not (openid and event_key):
        return

    match = _CHOOSE_TOPIC_RE.match(event_key)
    if not match:
        logger.info("非 CHOOSE_TOPIC 事件, 跳过: event_key=%s", event_key)
        return

    topic_id = int(match.group(1))
    topic = db.query(HotTopic).filter(HotTopic.id == topic_id).first()
    if topic is None:
        logger.warning("CLICK 选择的话题不存在: topic_id=%s", topic_id)
        return

    choice = UserChoice(
        user_openid=openid,
        topic_id=topic_id,
        choice_index=None,
    )
    db.add(choice)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.info(
            "用户重复选择: openid=%s topic_id=%s",
            hash_openid(openid), topic_id,
        )
        return

    # PENDING / PUSHED → SELECTED 正常推进, 不覆盖 PROCESSED
    if topic.status in (TopicStatus.PENDING, TopicStatus.PUSHED):
        topic.status = TopicStatus.SELECTED

    db.commit()
    # 点击菜单是互动, 刷新 last_interact_time
    _touch_interact(db, openid)
    logger.info(
        "CLICK 写入 user_choice 成功: openid=%s topic_id=%s",
        hash_openid(openid), topic_id,
    )


def _build_text_reply(to_user: str, from_user: str, content: str) -> str:
    """构造被动回复 XML。

    安全说明: 用户输入可能包含 ]]>，这会破坏 CDATA section。
    替换为 ]]&gt; 防止 XML 解析错误。
    """
    import time as _time

    # 替换 CDATA 结束标记，防止破坏 XML 结构
    safe_content = content.replace("]]>", "]]&gt;")

    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(_time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{safe_content}]]></Content>"
        "</xml>"
    )


@router.get("/health")
async def webhook_health() -> dict:
    return {"status": "ok"}
