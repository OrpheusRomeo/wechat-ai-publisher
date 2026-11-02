# app/services/daily_digest.py
"""每日话题日报 (D 方案): 把今日 top 话题拼成 1 篇文章, 群发到公众号.

每条话题的配文 (30-40 字简讯) 由 DeepSeek 生成, 调用方在传入前预先
按 ``{topic_id: brief_text}`` 准备好. 没生成的用原标题兜底.

输出格式 (微信公众号 HTML, 用户约定):

    2026-07-21 · 百度热搜 TOP 10

    1. 热搜标题   ← 蓝色加粗
       简讯内容   ← 绿色
       🔗 原文链接 ← 紫色可点击超链接 (真正的新闻页, 不是百度搜索聚合页)

    2. ...

颜色与编号常量集中在 ``_COLORS`` 与 ``_FORMAT`` 处, 调风格只动那一块.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Iterable, List, Mapping, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DIGEST_MAX_CHARS = 6000  # 群发文章保守上限 (字体调大后单条占用增加, 4000 截掉了 item 8-10)
_TITLE_MAX_CHARS = 60    # 单条话题标题软上限
# 个人订阅号 draft/add 标题硬上限实测 17 字符 (修复 L12), 留 2 字余量
_TITLE_MAX_CHARS_WEIXIN = 15
_BRIEF_MAX_CHARS = 50     # 单条简讯软上限

# ---- 视觉常量 (内置默认, env 没配就回退到这里) ---------------------------
_DEFAULT_COLORS = {
    "title":   "#2C5F8D",  # 深薄荷蓝: 热搜词
    "brief":   "#5F8D4E",  # 草色:    简讯
    "link":    "#7D2E8E",  # 南京大学紫: 链接
    "header":  "#555555",  # 灰: 顶部一行小字
}
_FONT_FAMILY = (
    "-apple-system, BlinkMacSystemFont, 'PingFang SC', "
    "'Microsoft YaHei', sans-serif"
)


def _resolve_colors() -> dict:
    """从 settings (即 .env) 读颜色, 缺省 / 解析失败回退内置默认.

    调用时机: 每次 ``build_digest()`` 入口拿一次, 避免 import 时未初始化
    settings, 同时支持运行期改 env 后 reload.
    """
    try:
        from app.core.config import settings
        out = dict(_DEFAULT_COLORS)
        for key, attr in (
            ("title", "wechat_digest_color_title"),
            ("brief", "wechat_digest_color_brief"),
            ("link",  "wechat_digest_color_link"),
        ):
            v = getattr(settings, attr, None) or ""
            if _looks_like_color(v):
                out[key] = v
            # 拿不到或不是颜色字符串, 保持 _DEFAULT_COLORS
        return out
    except Exception:
        # settings 不可用 (无 DB / 测试单文件运行), 用内置默认
        return dict(_DEFAULT_COLORS)


def _looks_like_color(s: str) -> bool:
    """粗校验: 接受 #RGB / #RRGGBB / rgb(...)/ rgba(...)/ 16+ 种 CSS named color."""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    if s.startswith("#") and len(s) in (4, 7) and all(
        c in "0123456789abcdefABCDEF" for c in s[1:]
    ):
        return True
    if s.startswith(("rgb(", "rgba(", "hsl(", "hsla(")):
        return s.endswith(")")
    # 简单的 named color (白名单最常用 8 种, 防止笔误脏数据注入)
    return s.lower() in {
        "black", "white", "red", "green", "blue", "yellow",
        "orange", "purple", "gray", "grey", "silver",
    }


def _esc(text: str) -> str:
    """HTML 转义, 防止用户输入把 markup 撑坏."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _is_baidu_aggregator(url: str) -> bool:
    """True 表示这是百度搜索/聚合页, **不是**真正的新闻原文 URL.

    判定条件 (任一命中即可):
    - host 命中 baidu.com 且 path 以 /s 或 /link 开头 (聚合/中转页)
    - URL 含 ``sa=fyb_news`` (热搜新闻视图参数, 百度聚合页标志)
    """
    if not url:
        return True
    try:
        u = urlparse(url)
    except Exception:
        return True
    host = (u.netloc or "").lower()
    path = (u.path or "").lower()
    if host.endswith("baidu.com") and (path.startswith("/s") or path.startswith("/link")):
        return True
    if "sa=fyb_news" in url:
        return True
    return False


# 个人/自媒体域名, 用户约定 "不要个人文章". 命中这里一律视为不合格 URL.
_PERSONAL_MEDIA_DENY = (
    "baijiahao.baidu.com",       # 百度百家号
    "mbd.baidu.com",             # 百家号移动详情
    "zhihu.com",                 # 知乎
    "zhuanlan.zhihu.com",        # 知乎专栏
    "douyin.com", "iesdouyin.com",
    "xiaohongshu.com",
    "bilibili.com",
)


def _is_personal_media(url: str) -> bool:
    """True 表示 URL 指向个人/自媒体平台, 不展示在 digest 中."""
    if not url:
        return True
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return True
    return any(d in host for d in _PERSONAL_MEDIA_DENY)


def _pick_real_news_url(topic) -> str:
    """从 topic 里挑出"可能是真新闻页"的 URL, 没合适就返回空串.

    优先 ``extra_metadata.news_url``; 不是百度聚合页/个人媒体就采纳,
    否则回退 ``source_url`` (``rawUrl``), 还不行就空串.
    """
    meta = getattr(topic, "extra_metadata", None) or {}
    news_url = (meta.get("news_url") or "").strip()
    if news_url and not _is_baidu_aggregator(news_url) and not _is_personal_media(news_url):
        return news_url
    src = (getattr(topic, "source_url", "") or "").strip()
    if src and not _is_baidu_aggregator(src) and not _is_personal_media(src):
        return src
    return ""


def _truncate_to_chars(text: str, max_chars: int) -> str:
    """按字符数截断字符串 (微信文章标题的硬限制)."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _truncate_brief(text: str, max_chars: int = 50) -> str:
    """简讯过长时截断, 长度优先级: 30-40 字符."""
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip("，,。 ") + "…"


class DailyDigestService:
    """纯函数式的日报拼装服务, 不持有状态."""

    @staticmethod
    def build_digest(
        topics: List,
        briefs: Mapping[int, str] | None = None,
        app_name: str = "AI 早报",
        digest_date: _date | None = None,
    ) -> Tuple[str, str, str, int, str]:
        """把 top 话题拼成一篇公众号 HTML 群发文章.

        返回: ``(title, content_html, summary, topic_count, cover_img_url)``

        - ``topics``: 类 HotTopic 的对象 (有 ``.id``, ``.title``,
          ``.heat_score``, ``.source_url``, ``.extra_metadata``)
        - ``briefs``: {topic_id: deepseek_brief_text}, 没有就回退到话题标题
        - ``cover_img_url``: 第一条话题配图 URL; 没有则 ""

        视觉约定:
            热搜词 = 蓝色加粗, 简讯 = 绿色, 链接 = 紫色可点击超链接,
            链接取 ``extra_metadata.news_url`` (真正的新闻原文页),
            拿不到再回退到 ``source_url`` (百度热搜聚合页).
        """
        d = digest_date or _date.today()
        date_compact = d.strftime("%Y-%m-%d")
        topic_count = len(topics or [])

        # 颜色每调用拿一次 (支持 reload 后看新配色)
        colors = _resolve_colors()

        title = f"{date_compact} 热搜早报"
        title = _truncate_to_chars(title, _TITLE_MAX_CHARS_WEIXIN)

        cover_img_url = ""
        if topics:
            meta = getattr(topics[0], "extra_metadata", None) or {}
            cover_img_url = (meta.get("img_url") or "").strip()

        briefs = briefs or {}

        # 顶部一行小字 + 容器: 全局字体/字号, 给下面每个段落一致
        if not topics:
            summary = f"{date_compact} 暂无热搜, 请稍后再来"
            content = (
                "<section>"
                "<p style=\"text-align:center;color:#888;font-size:16px;\">"
                "今日暂无热搜数据, 请稍后再来看看 👀"
                "</p>"
                "<p style=\"text-align:center;color:#888;font-size:15px;\">"
                "每天 09:00 自动更新, 点击公众号菜单「今日话题」"
                "选择感兴趣的话题, AI 为您生成深度文章。"
                "</p>"
                "</section>"
            )
            return title, content, summary, 0, ""

        # 容器 style: 字号 + 行高, 各段落继承
        body_open = (
            "<section "
            f"style=\"font-family:{_FONT_FAMILY};"
            "font-size:18px;line-height:1.7;color:#333;\">"
        )
        header = (
            f"<p style=\"text-align:center;color:{colors['header']};"
            "font-size:15px;margin:0 0 18px;\">"
            f"{date_compact} · 百度热搜 TOP {topic_count}"
            "</p>"
        )

        blocks: List[str] = []
        for idx, topic in enumerate(topics, start=1):
            raw_title = (getattr(topic, "title", "") or "").replace("\n", " ").strip()
            if len(raw_title) > _TITLE_MAX_CHARS:
                head_title = raw_title[: _TITLE_MAX_CHARS - 3] + "..."
            else:
                head_title = raw_title

            brief = _truncate_brief(
                briefs.get(getattr(topic, "id", None))
                or briefs.get(idx)
                or ""
            )
            if not brief:
                # 没生成简讯则用原标题做兜底, 至少非空
                brief = _truncate_brief(raw_title)

            meta = getattr(topic, "extra_metadata", None) or {}
            # 真实新闻页优先, 其次回退到 source_url; 都是百度聚合页就空
            url = _pick_real_news_url(topic)

            # 蓝 + 加粗 标题
            title_html = (
                "<p style=\"margin:8px 0 4px;\">"
                "<strong>"
                f"<span style=\"color:{colors['title']};\">"
                f"{idx}. {_esc(head_title)}"
                "</span>"
                "</strong>"
                "</p>"
            )
            # 绿 简讯 (如果有就显示, 没有就跳过这条)
            if brief:
                brief_html = (
                    "<p style=\"margin:2px 0;padding-left:16px;\">"
                    f"<span style=\"color:{colors['brief']};"
                    f"font-size:17px;\">{_esc(brief)}</span>"
                    "</p>"
                )
            else:
                brief_html = ""

            # 紫 可点击链接 (微信会把外链过一层中间页跳转, 用户能直接点击)
            if url:
                link_html = (
                    "<p style=\"margin:2px 0 10px;padding-left:16px;"
                    "font-size:16px;\">"
                    f"<span style=\"color:{colors['link']};\">"
                    "🔗 </span>"
                    f"<a href=\"{_esc(url)}\" target=\"_blank\" "
                    f"style=\"color:{colors['link']};"
                    "text-decoration:underline;\">"
                    "原文链接"
                    "</a>"
                    "</p>"
                )
            else:
                # 没拿到真正的新闻页 (爬虫被 captcha / 头条结构变了),
                # 兜底给一个灰色的"百度热搜"文字提示, 不点击
                link_html = (
                    "<p style=\"margin:2px 0 10px;padding-left:16px;"
                    "font-size:14px;color:#999;\">"
                    "📰 暂未拿到可点击的原文链接"
                    "</p>"
                )

            blocks.append(title_html + brief_html + link_html)

        content = body_open + header + "".join(blocks) + "</section>"

        # 超长兜底 (微信草稿理论不限, 但 6000+ 字符以上排版会变).
        # 截断点选在最近的 </p> 边界, 避免把 link 标签截成半截.
        if len(content) > _DIGEST_MAX_CHARS:
            cut = content[: _DIGEST_MAX_CHARS].rfind("</p>")
            if cut == -1:
                cut = _DIGEST_MAX_CHARS
            content = content[: cut + len("</p>")] + "</section>"

        # summary (公众号后台"摘要"字段, 不参与渲染)
        first_title = (getattr(topics[0], "title", "") or "").strip()
        if len(first_title) > 20:
            first_title = first_title[:17] + "..."
        summary = f"{first_title} 等 {topic_count} 条热搜"
        if len(summary) > 54:
            summary = summary[:51] + "..."

        return title, content, summary, topic_count, cover_img_url
