"""HTTP 接口/纯函数测试, 无需 PG/Redis/Kafka.

``RUN_INTEGRATION=1`` 时启用需要真实依赖的端点冒烟.
"""
import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from fastapi.testclient import TestClient

# 这些是离线的, 不需要跑 ``app.main`` 的 lifespan (会触发 Redis 连接)
from app.api.v1 import webhook as webhook_mod
from app.api.v1.webhook import (
    _WELCOME_MAX_CHARS,
    _build_text_reply,
    _build_welcome_message,
    _verify_signature,
)


# =============================================================================
# 离线测试: 不依赖 uvicorn / 任何外部服务
# =============================================================================

def test_wechat_signature_official_docs_example():
    """按微信公众号文档示例, 校验 sig 计算正确."""
    # 微信官方示例: token=「Token」, 但签名验证逻辑是 sha1(sort(...))
    token = "TokenTest"
    ts = "1700000000"
    nonce = "NonceTest"
    items = sorted([token, ts, nonce])
    sig = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
    assert _verify_signature(sig, ts, nonce, token) is True
    assert _verify_signature("0" * 40, ts, nonce, token) is False


# =============================================================================
# Plan B: 关注时被动回复欢迎语 (含今日 top 话题)
# =============================================================================

def _make_topic(title: str, heat: float) -> MagicMock:
    """构造一个形状类似 HotTopic 的 MagicMock, _build_welcome_message 只读
    .title 和 .heat_score, 其它字段不影响, 用 mock 避免触发 SQLAlchemy 元数据."""
    t = MagicMock()
    t.title = title
    t.heat_score = heat
    return t


def test_welcome_message_empty_topics_fallback():
    """0 条话题: 返回降级文案, 仍欢迎用户."""
    text = _build_welcome_message([], app_name="测试号")
    assert "欢迎关注 测试号" in text
    assert "今日暂无话题" in text
    # 仍给出引导, 让用户知道后续能做什么
    assert "历史" in text
    # 不应包含数字编号
    assert "1." not in text


def test_welcome_message_with_topics_lists_titles_and_heat():
    """正常路径: 编号 + 标题 + 热度都要出现."""
    topics = [
        _make_topic("热搜一", 12345.0),
        _make_topic("热搜二", 9999.0),
    ]
    text = _build_welcome_message(topics, app_name="AI 早报")
    assert "欢迎关注 AI 早报" in text
    assert "TOP 2" in text
    assert "1. 热搜一  🔥12345" in text
    assert "2. 热搜二  🔥9999" in text
    # 引导用户操作
    assert "回复数字" in text or "选择感兴趣" in text


def test_welcome_message_truncates_long_title():
    """单条标题 > 60 字符时, 截断成 57 + '...', 避免撑爆文案."""
    long_title = "x" * 200
    topics = [_make_topic(long_title, 100.0)]
    text = _build_welcome_message(topics, app_name="X")
    # 列表项中不应出现完整的 200 x
    assert "x" * 60 not in text
    assert "..." in text


def test_welcome_message_strips_newline_in_title():
    """标题里有换行会被替换成空格, 不会破坏列表格式."""
    topics = [_make_topic("第一行\n第二行", 100.0)]
    text = _build_welcome_message(topics, app_name="X")
    # 不会出现裸的 \n 在标题里
    assert "1. 第一行 第二行" in text


def test_welcome_message_under_char_limit():
    """极端情况: 10 条超长话题, 整段文案仍 ≤ _WELCOME_MAX_CHARS."""
    topics = [_make_topic("长标题" * 20, 99999.0) for _ in range(10)]
    text = _build_welcome_message(topics, app_name="X")
    assert len(text) <= _WELCOME_MAX_CHARS


def test_welcome_message_heat_is_integer_in_text():
    """heat_score 浮点会被强转 int, 文案里不出现小数点."""
    topics = [_make_topic("topic", 1234.9)]
    text = _build_welcome_message(topics, app_name="X")
    assert "🔥1234" in text
    assert "1234.9" not in text


def test_subscribe_event_returns_text_reply_xml(monkeypatch):
    """POST /webhook/wechat 收到 subscribe 事件, 应返回被动回复 XML.

    走 fastapi TestClient + dependency_overrides 注入假 db session,
    避免连真实 PG.
    """
    from fastapi import FastAPI
    from app.api.v1.webhook import router
    from app.api.deps import get_db

    # 与 app/main.py 保持一致: prefix="/webhook"
    app = FastAPI()
    app.include_router(router, prefix="/webhook")

    # mock db session: query 一次返回空 list, 触发降级欢迎语
    fake_db = MagicMock()
    query_chain = MagicMock()
    query_chain.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    query_chain.filter.return_value.all.return_value = []
    fake_db.query.return_value = query_chain

    def _override_db():
        try:
            yield fake_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    xml_body = (
        "<xml>"
        "<ToUserName><![CDATA[gh_official_id]]></ToUserName>"
        "<FromUserName><![CDATA[user_openid_001]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[subscribe]]></Event>"
        "</xml>"
    )
    resp = client.post("/webhook/wechat", content=xml_body)
    assert resp.status_code == 200
    body = resp.text
    # 返回的是 XML, 含 ToUserName(原 FromUserName) + 欢迎语
    assert "<MsgType><![CDATA[text]]></MsgType>" in body
    assert "user_openid_001" in body  # 收信人是用户
    assert "gh_official_id" in body    # 发信人是公众号
    assert "欢迎关注" in body

    # _handle_subscribe 应当被调用过 (upsert + commit 至少 1 次)
    assert fake_db.add.called or fake_db.commit.called


def test_subscribe_event_with_topics_includes_title(monkeypatch):
    """POST /webhook/wechat 收到 subscribe 事件, 话题非空时文案应含话题标题."""
    from fastapi import FastAPI
    from app.api.v1.webhook import router
    from app.api.deps import get_db

    # 与 app/main.py 保持一致: prefix="/webhook"
    app = FastAPI()
    app.include_router(router, prefix="/webhook")

    topics = [_make_topic("特朗普当选", 88888.0)]
    fake_db = MagicMock()

    # 第一次 query 走"今日 top", 返回有数据, 不走降级
    today_chain = MagicMock()
    today_chain.filter.return_value.order_by.return_value.limit.return_value.all.return_value = topics
    fake_db.query.return_value = today_chain

    def _override_db():
        try:
            yield fake_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    xml_body = (
        "<xml>"
        "<ToUserName><![CDATA[gh_x]]></ToUserName>"
        "<FromUserName><![CDATA[oUser1]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime>"
        "<MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[subscribe]]></Event>"
        "</xml>"
    )
    resp = client.post("/webhook/wechat", content=xml_body)
    assert resp.status_code == 200
    assert "特朗普当选" in resp.text
    assert "88888" in resp.text


# =============================================================================
# 集成测试 (需要 RUN_INTEGRATION=1 + 真实 PG/Redis/Kafka)
# =============================================================================

@pytest.fixture(scope="module")
def client():
    if not os.getenv("RUN_INTEGRATION"):
        pytest.skip("需要实际依赖 (Postgres/Redis/Kafka)，设置 RUN_INTEGRATION=1 启用")
    from app.main import app
    return TestClient(app)


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_list_topics_requires_admin_token(client):
    """Phase 4 (S3+M1) 加固后, /api/v1/* 必须带 X-Admin-Token."""
    resp = client.get("/api/v1/topics/")
    assert resp.status_code == 403
    resp = client.get(
        "/api/v1/topics/",
        headers={"X-Admin-Token": os.environ.get("ADMIN_API_KEY", "")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
