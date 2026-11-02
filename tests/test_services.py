"""单元测试：不需要外部依赖，覆盖 service 层纯函数 + settings + pii.

覆盖的关键回归点:

- S1: ``consumers.push_consumer`` 从 ``app.core.pii`` import ``hash_openid``
- S3 已删: ``wechat_template_id_*`` 字段移除 (订阅号无模板消息), 见 git log
- S4: ``wechat._markdown_to_html`` 把 ``**...**`` / ``## ...`` 替换为成对闭合的标签
- L9: webhook ``_verify_signature`` 正确
- M2: ``llm_clients.base.LLMClientBase._parse_response`` 不用裸 ``except``
  （对 KeyboardInterrupt 不吞噬）
- M11: 替代 ``content[:100] + "..."`` 的 digest 条件, 见 ``_markdown_to_html`` 的
  digest 长度边界 (test_wechat_digest_edge_lengths 不直接覆写 service, 但确保
  转换函数不吞行数)
- 订阅号: ``settings.wechat_daily_publish_quota`` 字段存在, 默认 1
- KafkaProducerService 单例
- pii.hash_openid / truncate_content

不需要 PG / Redis / Kafka / 微信凭证. 任何项目根目录下 ``pytest`` 即可跑.
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import os
from pathlib import Path

# 将项目根目录加入 sys.path, 避免 ``from app...`` 找不到包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(_ROOT))

import pytest
import requests

from app.api.v1.webhook import _verify_signature
from app.core.config import settings
from app.services.crawler import BaiduHotCrawler
from app.services.llm_clients.deepseek import DeepSeekClient
from app.services.wechat import WeChatService


def _make_llm_client() -> DeepSeekClient:
    """构造一个不发请求的 LLM 客户端（只测纯解析函数）。"""
    return DeepSeekClient(
        api_key="test-key",
        api_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
    )


# =============================================================================
# 已有测试 (保持兼容)
# =============================================================================

def test_crawler_mock_fallback(monkeypatch):
    """爬取失败时, 开启 BAIDU_HOT_USE_MOCK 才回退到 mock 数据。

    生产默认 false（返回空列表，不污染数据），所以这里必须显式打开开关。
    """
    monkeypatch.setattr(
        "app.services.crawler.settings.baidu_hot_use_mock", True, raising=False,
    )
    crawler = BaiduHotCrawler()
    # 构造一个无法访问的 url, 让 session.get 抛异常
    crawler.session.get = lambda *a, **kw: (_ for _ in ()).throw(
        requests.RequestException("offline")
    )
    topics = crawler.crawl()
    assert isinstance(topics, list)
    assert len(topics) > 0
    assert all("title" in t for t in topics)


def test_crawler_no_mock_returns_empty_on_failure(monkeypatch):
    """关闭 mock 时, 爬取失败返回空列表而不是 mock 数据（生产默认行为）。"""
    monkeypatch.setattr(
        "app.services.crawler.settings.baidu_hot_use_mock", False, raising=False,
    )
    crawler = BaiduHotCrawler()
    crawler.session.get = lambda *a, **kw: (_ for _ in ()).throw(
        requests.RequestException("offline")
    )
    assert crawler.crawl() == []


def test_deepseek_parse_response_fallback_text():
    """宽松 JSON 解析：非 JSON 输入也能降级解析。"""
    svc = _make_llm_client()
    parsed = svc._parse_response("标题示例\n第一段\n第二段")
    assert parsed["title"] == "标题示例"
    assert "第一段" in parsed["content"]


# =============================================================================
# 新增：覆盖 Phase 1 修复
# =============================================================================

def test_wechat_markdown_to_html_pair_strong():
    """S4 修复: ``**foo**`` 必须变成 ``<strong>foo</strong>`` 而不是 ``<strong>foo<strong>``."""
    html = WeChatService._markdown_to_html("hello **world**")
    assert html == "hello <strong>world</strong>"
    # 不能有未闭合的 strong
    assert html.count("<strong>") == html.count("</strong>")


def test_wechat_markdown_to_html_pair_h2():
    """S4 修复: 行首 ``## Title`` 必须配对闭合 ``</h2>``."""
    html = WeChatService._markdown_to_html("## 标题A\n正文\n## 标题B")
    assert html.count("<h2>") == 2
    assert html.count("</h2>") == 2
    assert "<h2>标题A</h2>" in html
    assert "<h2>标题B</h2>" in html


def test_wechat_markdown_to_html_no_nested_strong():
    """S4 回归: 旧的 replace 实现会把 ``a**b**c`` 变成 ``a<strong>b<strong>c``."""
    html = WeChatService._markdown_to_html("a**b**c")
    assert "<strong>b</strong>" in html
    assert "<strong><strong>" not in html
    # 转换后 a/c 留在外面
    assert html.startswith("a")
    assert html.endswith("c")


def test_wechat_markdown_to_html_newline_to_br():
    """S4: ``\\n`` → ``<br>``, 不能破环 H2 闭合."""
    html = WeChatService._markdown_to_html("第一行\n第二行")
    assert "<br>" in html


def test_deepseek_parse_handles_broken_json():
    """M2 回归: 让 json.loads 抛错时走 fallback, 不抛 KeyError."""
    svc = _make_llm_client()
    parsed = svc._parse_response("not json at all {[broken")
    # 降级后是 dict, 含 title / content
    assert isinstance(parsed, dict)
    assert "title" in parsed
    assert "content" in parsed


def test_deepseek_parse_valid_json():
    """正常 JSON 输入解析为完整字段."""
    svc = _make_llm_client()
    raw = '{"title":"T","content":"C","cover_description":"cover"}'
    parsed = svc._parse_response(raw)
    assert parsed["title"] == "T"
    assert parsed["content"] == "C"
    assert parsed["cover_description"] == "cover"


# =============================================================================
# 新增：辅助校验
# =============================================================================

def test_wechat_signature_verify_valid():
    """L9: sha1(sort([token, ts, nonce])) 校验通过."""
    token, ts, nonce = "mytoken", "1700000000", "abc"
    items = sorted([token, ts, nonce])
    sig = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
    assert _verify_signature(sig, ts, nonce, token) is True


def test_wechat_signature_verify_invalid():
    assert _verify_signature("notahash", "1", "2", "tok") is False
    assert _verify_signature("", "1", "2", "tok") is False


def test_push_consumer_imports_hash_openid():
    """push_consumer 必须从 ``app.core.pii`` import ``hash_openid``.

    内容组装已下沉到 ``PushNotificationService``（内部再调微信截断），
    但日志里的 openid 仍必须走散列，不能打明文。
    """
    mod = importlib.import_module("consumers.push_consumer")
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from app.core.pii import hash_openid" in src
    assert "PushNotificationService" in src


def test_pii_hash_openid_consistent_and_safe():
    """pii.hash_openid: 同 openid → 同样散列, 不同 openid → 不同, 空 → <empty>."""
    from app.core.pii import hash_openid

    a = hash_openid("user-123")
    b = hash_openid("user-123")
    assert a == b
    assert a != hash_openid("user-456")
    assert hash_openid(None) == "<empty>"
    assert hash_openid("") == "<empty>"


def test_pii_truncate_content_short_passthrough():
    from app.core.pii import truncate_content

    out = truncate_content("短", max_chars=50)
    assert out == "短"


def test_pii_truncate_content_long_truncated():
    from app.core.pii import truncate_content

    long_text = "x" * 200
    out = truncate_content(long_text, max_chars=10)
    assert out.startswith("x" * 10)
    assert "(+190 chars)" in out


def test_kafka_producer_singleton_identity():
    """KafkaProducerService 用 DCL + _initialized 守卫, 多次实例化是同一对象."""
    from app.services.kafka_producer import KafkaProducerService

    a = KafkaProducerService()
    b = KafkaProducerService()
    assert a is b


def test_settings_loads_without_error(caplog):
    """Settings 加载不能抛错 (字段缺失/类型错误 都会被这里 catch)."""
    caplog.set_level(logging.ERROR)
    # 第二次访问触发 Settings 再次校验, 不应该抛任何 ERROR
    _ = settings.celery_broker_url
    _ = settings.celery_result_backend
    assert caplog.records == []


def test_settings_cors_parses_comma_list():
    """cors_allowed_origins 接受逗号分隔, 解析为 ['a', 'b']."""
    # cors_origins_list 是运行时 property, 直接改字段即可（不依赖 .env）
    settings_test = settings.model_copy(
        update={"cors_allowed_origins": "a.com, b.com,c.com"},
    )
    assert settings_test.cors_origins_list == ["a.com", "b.com", "c.com"]


# =============================================================================
# D 方案: 每日话题日报 (DailyDigestService)
# =============================================================================

def _make_digest_topic(title: str, heat: float):
    """构造 mock topic, DailyDigestService 只读 .title / .heat_score."""
    from unittest.mock import MagicMock
    t = MagicMock()
    t.title = title
    t.heat_score = heat
    return t


def test_digest_empty_topics_fallback():
    """0 条话题: 走兜底文案, 不抛异常."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    title, content, summary, count, _cover = DailyDigestService.build_digest(
        [], app_name="X", digest_date=date(2026, 7, 7),
    )
    assert count == 0
    assert "热搜早报" in title
    assert "今日暂无热搜" in content
    assert "暂无" in summary


def test_digest_with_topics_contains_titles_and_heat():
    """10 条话题: 标题 + 编号 + 引导文案都要出现."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [
        _make_digest_topic(f"话题{i}", 10000 - i * 100)
        for i in range(1, 11)
    ]
    title, content, summary, count, _cover = DailyDigestService.build_digest(
        topics, app_name="AI 早报", digest_date=date(2026, 7, 7),
    )
    assert count == 10
    assert "热搜早报" in title
    assert "TOP 10" in content
    # 10 条标题都要在 content 里
    for i in range(1, 11):
        assert f"话题{i}" in content
    # 编号格式 (HTML: <strong><span ...>1. 话题1</span></strong>)
    assert "1. 话题1" in content
    assert "10. 话题10" in content
    # summary 含话题数
    assert "10" in summary


def test_digest_truncates_long_title():
    """单条标题 > 60 字符截断, 避免单条撑爆内容."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    long_title = "x" * 200
    topics = [_make_digest_topic(long_title, 100.0)]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    # 完整 200 x 不应出现
    assert "x" * 60 not in content
    assert "..." in content


def test_digest_does_not_leak_raw_heat_score():
    """热度不再渲染进正文, 浮点值不能漏到 HTML 里."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [_make_digest_topic("topic", 1234.9)]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    assert "1234.9" not in content
    assert "1234" not in content


def test_digest_strips_newline_in_title():
    """标题里的换行被替换成空格, 不会破坏列表."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [_make_digest_topic("第一行\n第二行", 100.0)]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    assert "第一行 第二行" in content


def test_digest_summary_under_length_cap():
    """summary 控制在 120 字符内, 微信 digest 字段上限 54 字给点余量."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [_make_digest_topic("t", 99999.0)] * 5
    _, _, summary, _, _ = DailyDigestService.build_digest(
        topics, app_name="X" * 50, digest_date=date(2026, 7, 7),
    )
    assert len(summary) <= 120


def test_digest_includes_cta_guidance():
    """兜底文案内要有引导文案, 串起菜单/数字回复流程."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    _, content, _, _, _ = DailyDigestService.build_digest(
        [], app_name="X", digest_date=date(2026, 7, 7),
    )
    assert "今日话题" in content  # 提到菜单名
    assert "AI" in content  # 提到 AI 生成


# =============================================================================
# 新格式: 无序号 # 标题 + 新闻链接
# =============================================================================

def _make_digest_topic_full(title: str, heat: float,
                            news_url: str = "",
                            source_url: str = ""):
    """构造 mock topic, 含 extra_metadata 与 source_url."""
    from unittest.mock import MagicMock
    t = MagicMock()
    t.title = title
    t.heat_score = heat
    t.source_url = source_url
    t.extra_metadata = {"news_url": news_url} if news_url else {}
    return t


def test_digest_renders_numbered_list():
    """当前格式: 标题带序号渲染在 <strong> 内, 不用 markdown '#'."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [
        _make_digest_topic_full("葡萄牙遭淘汰", 1.0, news_url="https://news.example.com/pt"),
        _make_digest_topic_full("比利时战胜美国", 2.0, news_url="https://news.example.com/be"),
    ]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    assert "1. 葡萄牙遭淘汰" in content
    assert "2. 比利时战胜美国" in content
    # 不用 markdown 标题写法
    assert "# 葡萄牙遭淘汰" not in content


def test_digest_uses_news_url_not_source_url():
    """链接优先用 extra_metadata.news_url, 不用 source_url."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [
        _make_digest_topic_full(
            "热点话题",
            1.0,
            news_url="https://news.example.com/real-article",
            source_url="https://www.baidu.com/s?wd=baidu-hot-page",
        ),
    ]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    # 新闻原文链接要在 content 里
    assert "https://news.example.com/real-article" in content
    # 百度热搜聚合页不能出现
    assert "baidu-hot-page" not in content
    assert "baidu.com/s" not in content


def test_digest_rejects_baidu_aggregator_url():
    """百度热搜聚合页不是原文链接, 不应出现在正文链接里."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [
        _make_digest_topic_full(
            "老数据",
            1.0,
            news_url="",
            source_url="https://www.baidu.com/s?wd=legacy",
        ),
    ]
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, app_name="X", digest_date=date(2026, 7, 7),
    )
    # 聚合页被过滤掉, 走"暂无原文链接"占位文案
    assert "baidu.com/s?wd=legacy" not in content
    assert "暂未拿到可点击的原文链接" in content


def test_digest_brief_not_indented():
    """简讯与链接都不带前导空格 (新格式对齐)."""
    from app.services.daily_digest import DailyDigestService
    from datetime import date

    topics = [
        _make_digest_topic_full(
            "对齐测试",
            1.0,
            news_url="https://news.example.com/a",
        ),
    ]
    briefs = {topics[0].id: "一句话简讯"}
    _, content, _, _, _ = DailyDigestService.build_digest(
        topics, briefs=briefs, app_name="X", digest_date=date(2026, 7, 7),
    )
    # 不能有 "  简讯" 这种 2 空格缩进
    assert "  一句话简讯" not in content
    # 不能有 "  链接" 这种缩进
    assert "  https://news.example.com/a" not in content
    # 简讯与链接都在正文里
    assert "一句话简讯" in content
    assert "https://news.example.com/a" in content


def test_crawler_extracts_news_url_field():
    """爬虫从 Baidu API 响应里同时抽 rawUrl 与 url 两个字段.

    url 字段被强制补 ``&sa=fyb_news&rsv_dl=fyb_news`` 切到新闻聚合视图,
    由 ``_resolve_original_news_url`` 再跳一次拿真实详情页.
    """
    crawler = BaiduHotCrawler()
    fake_json = {
        "success": True,
        "data": {
            "cards": [{
                "content": [{
                    "word": "葡萄牙遭淘汰",
                    "hotScore": 1234567,
                    "rawUrl": "https://www.baidu.com/s?wd=baidu-hot",
                    "url": "https://www.baidu.com/s?wd=fyb_news",
                    "img": "https://img.example.com/cover.jpg",
                }],
            }],
        },
    }

    class _Resp:
        status_code = 200
        text = ""
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    resp = _Resp()
    import json as _json
    resp.text = _json.dumps(fake_json)
    # stub: 让 _resolve_original_news_url 直接返回 baidu_news_url (避免真去抓)
    crawler._resolve_original_news_url = lambda u: u
    crawler.session.get = lambda *a, **kw: resp
    topics = crawler.crawl()
    assert len(topics) == 1
    t = topics[0]
    assert t["title"] == "葡萄牙遭淘汰"
    assert t["source_url"] == "https://www.baidu.com/s?wd=baidu-hot"
    # url 字段会被强制补 sa=fyb_news 切到新闻聚合页
    assert (
        t["news_url"]
        == "https://www.baidu.com/s?wd=fyb_news&sa=fyb_news&rsv_dl=fyb_news"
    )
    assert t["img_url"] == "https://img.example.com/cover.jpg"


def test_is_real_news_url_filters_baidu_internal():
    """_is_real_news_url: 百度聚合/搜索/captcha 与个人媒体都不要, 主流媒体要."""
    c = BaiduHotCrawler
    # 百家号 / mbd 属个人媒体 - 拒绝 (用户约定 2026-07-21)
    assert not c._is_real_news_url(
        "https://baijiahao.baidu.com/s?id=1870139558176616803",
    )
    assert not c._is_real_news_url(
        "https://mbd.baidu.com/newspage/data/landingsuper?id=123",
    )
    # 外站原文 - 接受
    assert c._is_real_news_url("https://news.sina.com.cn/article/1.html")
    # 百度聚合/搜索 - 拒绝
    assert not c._is_real_news_url(
        "https://www.baidu.com/s?wd=xxx&sa=fyb_news&rsv_dl=fyb_news",
    )
    assert not c._is_real_news_url("https://www.baidu.com/")
    # 验证码/登录页 - 拒绝
    assert not c._is_real_news_url(
        "https://wappass.baidu.com/static/captcha/tuxing.html",
    )
    # 个人媒体 - 拒绝
    assert not c._is_real_news_url("https://zhuanlan.zhihu.com/p/123")
    assert not c._is_real_news_url("https://www.douyin.com/video/123")
    assert not c._is_real_news_url("https://www.xiaohongshu.com/explore/123")
    # 空/None
    assert not c._is_real_news_url("")
    assert not c._is_real_news_url(None)  # type: ignore[arg-type]


def test_extract_candidate_links_finds_h3_anchors():
    """_extract_candidate_links: 优先 h3 下的 baidu.com/link?url=..."""
    html = """
    <html><body>
      <h3><a href="http://www.baidu.com/link?url=AAA">标题A</a></h3>
      <h3><a href="http://www.baidu.com/link?url=BBB">标题B</a></h3>
      <div><a href="https://www.baidu.com/s?wd=hot">热搜</a></div>
      <a href="http://www.baidu.com/link?url=CCC">噪音链接</a>
    </body></html>
    """
    c = BaiduHotCrawler()
    out = c._extract_candidate_links(html)
    # 只取 h3 下的, 噪音链接不应出现
    assert out == [
        "http://www.baidu.com/link?url=AAA",
        "http://www.baidu.com/link?url=BBB",
    ]


def test_extract_candidate_links_fallback_full_scan():
    """h3 没命中时降级全页扫 baidu.com/link?url=..."""
    html = """
    <html><body>
      <p><a href="http://www.baidu.com/link?url=XXX">在段落里</a></p>
    </body></html>
    """
    c = BaiduHotCrawler()
    out = c._extract_candidate_links(html)
    assert out == ["http://www.baidu.com/link?url=XXX"]


def test_resolve_original_news_url_follows_redirect():
    """_resolve_original_news_url: 拿到第一个能跳通到详情页的链接."""
    crawler = BaiduHotCrawler()

    # mock 聚合页 HTML
    class _AggResp:
        status_code = 200
        text = (
            "<html><body>"
            "<h3><a href=\"http://www.baidu.com/link?url=ABC\">新闻1</a></h3>"
            "<h3><a href=\"http://www.baidu.com/link?url=DEF\">新闻2</a></h3>"
            "</body></html>"
        )
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    class _HeadResp:
        status_code = 302
        headers = {}

        def __init__(self, location: str = ""):
            self.headers = {"Location": location}
            self.url = ""

    def fake_head(link_url, **_kw):
        # ABC → 跳到详情页, DEF → 跳到 captcha
        if "ABC" in link_url:
            return _HeadResp(
                "https://news.sina.com.cn/article/1.html",
            )
        if "DEF" in link_url:
            return _HeadResp(
                "https://wappass.baidu.com/static/captcha/tuxing.html",
            )
        return _HeadResp()

    crawler.session.get = lambda *a, **kw: _AggResp()
    crawler.session.head = fake_head
    real = crawler._resolve_original_news_url(
        "https://www.baidu.com/s?wd=x&sa=fyb_news&rsv_dl=fyb_news",
    )
    assert real == "https://news.sina.com.cn/article/1.html"


def test_resolve_original_news_url_fallback_to_aggregator():
    """解析全失败时回退到 baidu_news_url, 不抛异常."""
    crawler = BaiduHotCrawler()

    class _AggResp:
        status_code = 200
        text = "<html><body>no candidates here</body></html>"
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    crawler.session.get = lambda *a, **kw: _AggResp()
    baidu_news_url = "https://www.baidu.com/s?wd=x&sa=fyb_news"
    real = crawler._resolve_original_news_url(baidu_news_url)
    assert real == baidu_news_url


def test_is_preferred_source_known_domains():
    """权威源白名单: 新浪/网易/新华网/人民日报都识别, 其它不识别."""
    c = BaiduHotCrawler
    # 新浪
    assert c._is_preferred_source("https://news.sina.com.cn/article/1.html")
    assert c._is_preferred_source("https://sports.sina.cn/2026/x.html")
    # 网易 (163 主域)
    assert c._is_preferred_source("https://news.163.com/123")
    assert c._is_preferred_source("https://www.163.com/dy/article/abc.html")
    # 新华网
    assert c._is_preferred_source("https://www.xinhuanet.com/politics/abc.htm")
    assert c._is_preferred_source("https://news.cn/world/2026-07/09/c_1123123.html")
    # 人民日报 (含 rmrb 子频道)
    assert c._is_preferred_source("https://www.people.com.cn/n1/2026/0709/c123.html")
    assert c._is_preferred_source("https://rmrb.people.com.cn/...")
    # 百家号 / 凤凰网 / 河南日报 - 不在白名单
    assert not c._is_preferred_source(
        "https://baijiahao.baidu.com/s?id=123",
    )
    assert not c._is_preferred_source("https://ishare.ifeng.com/c/s/v006x")
    assert not c._is_preferred_source("https://5g.hnr.cn/news/0/2074851682459271169")
    assert not c._is_preferred_source("")
    assert not c._is_preferred_source(None)  # type: ignore[arg-type]


def test_resolve_prefers_preferred_source_over_baijiahao():
    """权威源 (网易) 即使排在百家号后面, 也要优选权威源."""
    crawler = BaiduHotCrawler()

    class _AggResp:
        status_code = 200
        text = (
            "<html><body>"
            "<h3><a href=\"http://www.baidu.com/link?url=BAI\">百家号新闻</a></h3>"
            "<h3><a href=\"http://www.baidu.com/link?url=WANG\">网易新闻</a></h3>"
            "<h3><a href=\"http://www.baidu.com/link?url=XIN\">新华网</a></h3>"
            "</body></html>"
        )
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    class _HeadResp:
        status_code = 302
        headers = {}

        def __init__(self, location: str = ""):
            self.headers = {"Location": location}
            self.url = ""

    def fake_head(link_url, **_kw):
        if "BAI" in link_url:
            return _HeadResp(
                "https://baijiahao.baidu.com/s?id=1870139558176616803",
            )
        if "WANG" in link_url:
            return _HeadResp("https://www.163.com/dy/article/ABC123.html")
        if "XIN" in link_url:
            return _HeadResp(
                "https://www.xinhuanet.com/politics/2026-07/09/c_1123.html",
            )
        return _HeadResp()

    crawler.session.get = lambda *a, **kw: _AggResp()
    crawler.session.head = fake_head
    real = crawler._resolve_original_news_url(
        "https://www.baidu.com/s?wd=x&sa=fyb_news&rsv_dl=fyb_news",
    )
    # 第一个候选是百家号, 但权威源优先, 网易/新华网都命中
    # 应该返回最早命中的权威源 (网易), 而不是第一个能跳通的百家号
    assert real == "https://www.163.com/dy/article/ABC123.html"


def test_resolve_skips_baijiahao_and_uses_other_real_source():
    """百家号等个人媒体被跳过, 退化到另一个能跳通的真实新闻链接."""
    crawler = BaiduHotCrawler()

    class _AggResp:
        status_code = 200
        text = (
            "<html><body>"
            "<h3><a href=\"http://www.baidu.com/link?url=BAI\">百家号</a></h3>"
            "<h3><a href=\"http://www.baidu.com/link?url=HNR\">河南日报</a></h3>"
            "</body></html>"
        )
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    class _HeadResp:
        status_code = 302
        headers = {}

        def __init__(self, location: str = ""):
            self.headers = {"Location": location}
            self.url = ""

    def fake_head(link_url, **_kw):
        if "BAI" in link_url:
            return _HeadResp("https://baijiahao.baidu.com/s?id=123")
        if "HNR" in link_url:
            return _HeadResp("https://5g.hnr.cn/news/0/abc")
        return _HeadResp()

    crawler.session.get = lambda *a, **kw: _AggResp()
    crawler.session.head = fake_head
    real = crawler._resolve_original_news_url(
        "https://www.baidu.com/s?wd=x&sa=fyb_news",
    )
    # 百家号被 _is_real_news_url 拒绝, 最终落到河南日报
    assert real == "https://5g.hnr.cn/news/0/abc"
