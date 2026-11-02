"""单元测试：微信客户端唯一实现 (``app.services.wechat_core``) 及其适配器。

覆盖的关键回归点:

- 微信 HTTP 语义只有一份实现, ``app.services.wechat``（``.env`` 凭据）与
  ``app.services.platforms.wechat_service``（``platform_accounts`` 凭据）
  都只是薄适配器
- access_token 缓存按 appid 隔离, TTL 临近过期主动刷新
- 群发配额错误 (45009/45004) 不重试, token 失效 (40001 等) 刷 token 重试一次
- 封面支持本地文件路径 (``fetch_and_crop_cover`` 的产物), 默认封面缺失时报错而非静默
- 素材 / token 的 Redis key 按公众号隔离
- 「建草稿 → 群发」的状态映射 (published / draft) 与 48001、45009 分支
- 管理后台 system_settings 覆盖到运行时 settings

不需要 PG / Redis / Kafka / 微信凭证。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from app.core.config import settings
from app.services.wechat_core import (
    QUOTA_ERRCODES,
    TOKEN_INVALID_ERRCODES,
    WeChatApiClient,
    is_ip_whitelist_error,
)
from app.services.wechat_publish import (
    PublishOutcome,
    check_daily_quota,
    count_published_today,
    publish_wechat_draft,
    resolve_cover_url,
)


# =============================================================================
# 测试替身
# =============================================================================

class FakeRedis:
    """最小 Redis 替身，只实现用到的 get/set/ttl。"""

    def __init__(self) -> None:
        self.store: dict = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.set_calls += 1
        self.store[key] = value

    def ttl(self, key):
        return 3600


class FakeResponse:
    def __init__(self, payload, content=b""):
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _client(redis_conn=None, **kwargs) -> WeChatApiClient:
    return WeChatApiClient(
        "wx-test-appid",
        "secret",
        redis_conn=redis_conn or FakeRedis(),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_circuit_breaker(monkeypatch):
    """熔断器状态存在 Redis 里, 单测环境没有 Redis → 换成直通实现。"""
    class _NoopBreaker:
        def call(self, func, *args, fallback=None, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                if fallback is not None:
                    return fallback
                raise

    monkeypatch.setattr("app.services.wechat_core.wechat_cb", lambda: _NoopBreaker())


# =============================================================================
# access_token
# =============================================================================

def test_access_token_is_cached(monkeypatch):
    """拿到一次 token 后应写入 Redis, 第二次不再打网络。"""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return FakeResponse({"access_token": "tok-1", "expires_in": 7200})

    monkeypatch.setattr("app.services.wechat_core.requests.get", fake_get)
    redis_conn = FakeRedis()
    client = _client(redis_conn)

    assert client.get_access_token() == "tok-1"
    assert client.get_access_token() == "tok-1"
    assert len(calls) == 1
    assert "tok-1" in redis_conn.store.values()


def test_access_token_force_refresh_bypasses_cache(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return FakeResponse({"access_token": f"tok-{len(calls)}", "expires_in": 7200})

    monkeypatch.setattr("app.services.wechat_core.requests.get", fake_get)
    client = _client()

    client.get_access_token()
    client.get_access_token(force_refresh=True)
    assert len(calls) == 2


def test_access_token_cache_key_scoped_by_appid():
    """不同公众号的 token 不能共用同一个缓存 key。"""
    a = _client()
    b = WeChatApiClient("wx-other", "secret", redis_conn=FakeRedis())
    assert a._token_cache_key != b._token_cache_key
    assert "wx-test-appid" in a._token_cache_key


# =============================================================================
# 草稿 / 群发
# =============================================================================

def _stub_token(monkeypatch, client, token="tok"):
    monkeypatch.setattr(client, "get_access_token", lambda *a, **k: token)


def test_create_draft_requires_cover(monkeypatch):
    """拿不到封面时 create_draft 必须失败(微信 draft/add 强制要求 thumb_media_id)。"""
    client = _client()
    _stub_token(monkeypatch, client)
    monkeypatch.setattr(client, "_upload_cover_from_url", lambda *a, **k: None)
    monkeypatch.setattr(client, "_get_or_upload_default_cover", lambda *a, **k: None)

    assert client.create_draft("标题", "正文") is None


def test_create_draft_truncates_title_and_author(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        import json
        captured.update(json.loads(data.decode("utf-8")))
        return FakeResponse({"media_id": "media-1"})

    monkeypatch.setattr("app.services.wechat_core.requests.post", fake_post)
    client = _client()
    _stub_token(monkeypatch, client)
    monkeypatch.setattr(client, "_upload_cover_from_url", lambda *a, **k: "thumb-1")

    result = client.create_draft(
        "一" * 30, "正文内容", cover_url="https://x/y.jpg", author="很长很长的作者名",
    )

    assert result and result["media_id"] == "media-1"
    article = captured["articles"][0]
    assert len(article["title"]) == 17
    assert len(article["author"].encode("utf-8")) <= 8
    assert article["thumb_media_id"] == "thumb-1"


def test_publish_draft_quota_error_is_not_retried(monkeypatch):
    """45009/45004 是硬配额错误, 不能再打一次微信。"""
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        calls.append(url)
        return FakeResponse({"errcode": 45009, "errmsg": "reach max api daily quota"})

    monkeypatch.setattr("app.services.wechat_core.requests.post", fake_post)
    client = _client()
    _stub_token(monkeypatch, client)

    result = client.publish_draft("media-1")
    assert result["errcode"] == 45009
    assert len(calls) == 1
    assert 45009 in QUOTA_ERRCODES


def test_publish_draft_refreshes_token_once(monkeypatch):
    """token 失效 (40001) 应刷 token 后重试一次并成功。"""
    responses = [
        FakeResponse({"errcode": 40001, "errmsg": "invalid credential"}),
        FakeResponse({"errcode": 0, "publish_id": "pub-1"}),
    ]
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr("app.services.wechat_core.requests.post", fake_post)
    refreshed = []

    client = _client()
    monkeypatch.setattr(
        client, "get_access_token",
        lambda *a, **k: (refreshed.append(1), "tok")[1],
    )

    result = client.publish_draft("media-1")
    assert result["errcode"] == 0
    assert result["publish_id"] == "pub-1"
    assert len(calls) == 2
    assert refreshed


def test_token_invalid_errcodes_are_known():
    assert 40001 in TOKEN_INVALID_ERRCODES
    assert 42001 in TOKEN_INVALID_ERRCODES


def test_ip_whitelist_error_detection():
    assert is_ip_whitelist_error(40101)
    assert not is_ip_whitelist_error(40001)


# =============================================================================
# 封面素材
# =============================================================================

def test_upload_cover_supports_local_path(monkeypatch, tmp_path):
    """封面既可以是 URL 也可以是本地文件(fetch_and_crop_cover 的产物)。"""
    img = tmp_path / "cover.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    uploads = []

    def fake_post(url, data=None, headers=None, timeout=None, files=None, **kwargs):
        uploads.append(files)
        return FakeResponse({"media_id": "material-1"})

    monkeypatch.setattr("app.services.wechat_core.requests.post", fake_post)
    client = _client()
    _stub_token(monkeypatch, client)

    media_id = client._upload_cover_from_url(str(img))
    assert media_id == "material-1"
    assert uploads, "应走 multipart 上传"


def test_cover_cache_key_scoped_by_appid():
    a = _client()
    b = WeChatApiClient("wx-other", "secret", redis_conn=FakeRedis())
    assert a._cover_cache_key("https://x/y.jpg") != b._cover_cache_key("https://x/y.jpg")


def test_default_cover_falls_back_to_settings():
    """账号没配 default_cover_url 时回退到全局配置, 不能直接失败。"""
    assert settings.wechat_default_cover_url
    client = WeChatApiClient(
        "wx-test", "secret", redis_conn=FakeRedis(), default_cover_url=None,
    )
    assert client.default_cover_url == settings.wechat_default_cover_url


# =============================================================================
# 薄适配器
# =============================================================================

def test_env_adapter_delegates_to_core(monkeypatch):
    """``WeChatService`` 只是 ``WeChatApiClient`` 的适配器。"""
    from app.services.wechat import WeChatService

    svc = WeChatService()
    assert isinstance(svc.api_client, WeChatApiClient)
    assert svc.app_id == settings.wechat_app_id
    assert "<strong>x</strong>" in svc._markdown_to_html("**x**")


def test_platform_adapter_uses_appid_scope():
    """``WeChatPlatformService`` 的缓存作用域按 appid 隔离。"""
    from app.services.platforms.wechat_service import WeChatPlatformService

    class _FakePlatform:
        platform_type = "wechat"

    class _FakeAccount:
        account_id = "acc-1"
        platform = _FakePlatform()
        credentials_encrypted = None

    svc = WeChatPlatformService(_FakeAccount())
    # 没有可解密的凭证 → app_id 为空 → 退化成账号维度, 但仍不应抛异常
    assert svc.api_client.scope.startswith("account-")


# =============================================================================
# 统一发布流程
# =============================================================================

class _FakeWechat:
    def __init__(self, draft_result=None, publish_result=None, raise_on_draft=False):
        self.draft_result = draft_result
        self.publish_result = publish_result or {"errcode": 0, "publish_id": "pub-1"}
        self.raise_on_draft = raise_on_draft
        self.draft_calls = []

    def create_draft(self, **kwargs):
        self.draft_calls.append(kwargs)
        if self.raise_on_draft:
            raise RuntimeError("boom")
        return self.draft_result

    def publish_draft(self, media_id):
        return self.publish_result


def test_publish_wechat_draft_success():
    wechat = _FakeWechat(draft_result={"media_id": "media-1"})
    outcome = publish_wechat_draft(wechat, title="t", content="c")
    assert isinstance(outcome, PublishOutcome)
    assert outcome.published
    assert outcome.media_id == "media-1"
    assert outcome.publish_id == "pub-1"


def test_publish_wechat_draft_draft_creation_failed():
    wechat = _FakeWechat(draft_result=None)
    outcome = publish_wechat_draft(wechat, title="t", content="c")
    assert not outcome.published
    assert outcome.media_id is None
    assert "草稿" in outcome.error_msg


def test_publish_wechat_draft_quota_keeps_draft():
    wechat = _FakeWechat(
        draft_result={"media_id": "media-1"},
        publish_result={"errcode": 45009, "errmsg": "reach max api daily quota"},
    )
    outcome = publish_wechat_draft(wechat, title="t", content="c")
    assert not outcome.published
    assert outcome.errcode == 45009
    assert outcome.media_id == "media-1"
    assert "配额" in outcome.error_msg


def test_publish_wechat_draft_no_group_publish_permission():
    """48001: 订阅号无群发权限 → 草稿保留, 提示人工群发。"""
    wechat = _FakeWechat(
        draft_result={"media_id": "media-1"},
        publish_result={"errcode": 48001, "errmsg": "api unauthorized"},
    )
    outcome = publish_wechat_draft(wechat, title="t", content="c")
    assert not outcome.published
    assert outcome.errcode == 48001
    assert "草稿箱" in outcome.error_msg


# =============================================================================
# 封面解析 / 配额统计
# =============================================================================

class _FakeArticle:
    def __init__(self, cover_url=None, topic_id=None, id=1):
        self.cover_url = cover_url
        self.topic_id = topic_id
        self.id = id


class _FakeDB:
    def __init__(self, topic=None, published=0):
        self._topic = topic
        self._published = published
        self.filters = []

    def query(self, *args, **kwargs):
        return _FakeQuery(self)

    def _result(self):
        return self


class _FakeQuery:
    def __init__(self, db):
        self._db = db

    def filter(self, *args, **kwargs):
        self._db.filters.append(args)
        return self

    def first(self):
        return self._db._topic

    def count(self):
        return self._db._published


def test_resolve_cover_url_passthrough_http():
    article = _FakeArticle(cover_url="https://example.com/a.jpg")
    assert resolve_cover_url(article, _FakeDB()) == "https://example.com/a.jpg"


def test_resolve_cover_url_without_topic_returns_empty():
    article = _FakeArticle(cover_url="", topic_id=None)
    assert resolve_cover_url(article, _FakeDB()) == ""


def test_resolve_cover_url_disallow_local():
    """非微信平台不允许本地路径封面。"""
    article = _FakeArticle(cover_url="", topic_id=5)
    assert resolve_cover_url(article, _FakeDB(), allow_local=False) == ""


def test_count_published_today_scopes_by_account():
    """配额统计必须按发布目标隔离, 头条发布不能吃掉微信的群发配额。"""
    db = _FakeDB(published=0)
    count_published_today(db, None)
    env_sql = str(db.filters[-1][0])
    assert "IS NULL" in env_sql

    db = _FakeDB(published=0)
    count_published_today(db, 7)
    account_sql = str(db.filters[-1][0])
    assert "platform_account_id" in account_sql
    assert "IS NULL" not in account_sql


def test_check_daily_quota():
    db = _FakeDB(published=1)
    ok, published = check_daily_quota(db, 1)
    assert not ok and published == 1

    db = _FakeDB(published=0)
    ok, published = check_daily_quota(db, 1)
    assert ok and published == 0


# =============================================================================
# 管理后台 system_settings → 运行时 settings
# =============================================================================

def test_runtime_override_casting():
    from app.core.runtime_overrides import _cast

    assert _cast("bool", "true") is True
    assert _cast("bool", "FALSE") is False
    assert _cast("bool", "yes") is True
    assert _cast("bool", "maybe") is None
    assert _cast("int", "42") == 42
    assert _cast("int", "x") is None
    assert _cast("str", "abc") == "abc"


def test_runtime_override_applies_whitelisted_keys(monkeypatch):
    from app.core import runtime_overrides

    class _Row:
        def __init__(self, key, value, is_secret=False):
            self.key = key
            self.value_encrypted = value
            self.is_secret = is_secret

    monkeypatch.setattr(
        runtime_overrides,
        "_read_rows",
        lambda: [
            _Row("wechat_daily_publish_quota", "4"),
            _Row("wechat_enable_custom_message", "false"),
            _Row("scheduler_topic_limit", "20"),
            _Row("wechat_app_id", ""),          # 空值不覆盖
            _Row("not_a_setting", "x"),         # 不在白名单
        ],
    )
    monkeypatch.setattr(settings, "wechat_daily_publish_quota", 1)
    monkeypatch.setattr(settings, "wechat_enable_custom_message", True)
    monkeypatch.setattr(settings, "scheduler_topic_limit", 10)
    monkeypatch.setattr(settings, "wechat_app_id", "wx-from-env")

    applied = runtime_overrides.apply_runtime_overrides(force=True)

    assert applied == 3
    assert settings.wechat_daily_publish_quota == 4
    assert settings.wechat_enable_custom_message is False
    assert settings.scheduler_topic_limit == 20
    assert settings.wechat_app_id == "wx-from-env"


def test_runtime_override_survives_db_error(monkeypatch):
    """DB 不可用时不能让进程起不来。"""
    from app.core import runtime_overrides

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(runtime_overrides, "_read_rows", boom)
    assert runtime_overrides.apply_runtime_overrides(force=True) == 0


@pytest.mark.parametrize("key", ["deepseek_model", "enable_crawler"])
def test_runtime_override_ignores_dead_keys(key):
    """已删除的死配置不在白名单里。"""
    from app.core.runtime_overrides import _OVERRIDABLE

    assert key not in _OVERRIDABLE
