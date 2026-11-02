# app/services/crawler.py
"""百度热搜爬虫。
网络异常 / 内容为空时回退行为受 ``settings.baidu_hot_use_mock`` 控制：
- 生产环境建议 ``false``：此时返回空列表 + 警告日志，由上游决定要不要告警。
- 开发环境 / 沙箱 : ``true``：返回内置 mock 数据，便于联调。
【改造】改用百度原生JSON接口 https://top.baidu.com/api/board?platform=pc&tab=realtime，不再解析HTML页面
【news_url 改造】百度热搜 API 返回的 url 是 ``baidu.com/s?wd=...&sa=fyb_news`` 这种
聚合页，还要再跳一次 ``baidu.com/link?url=...`` 才能拿到真正的新闻详情页
（百家号 / mbd.baidu.com 等）。这一跳由 ``_resolve_original_news_url`` 完成,
失败回退到 baidu_news_url, 再不行用 rawUrl。
"""
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse
import re
import requests
import json
try:
    from app.core.config import settings
except ImportError:
    logger.error(
        "无法导入 app.core.config，爬虫将使用 mock 数据运行。"
        "请检查环境变量和 config 模块是否正确加载。"
    )
    class MockSettings:
        baidu_hot_use_mock = True
    settings = MockSettings()

logger = logging.getLogger(__name__)
# 替换为官方API接口地址
DEFAULT_API_URL = "https://top.baidu.com/api/board?platform=pc&tab=realtime"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# 浏览器完整请求头, 规避百度安全验证 (触发后页面变 CAPTCHA,
# 长度只有 1.5KB 没有任何结果链接). 加 Accept-Language / Referer 等头
# 让请求看起来像真实 Chrome 浏览器.
DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 注意: 不要加 br (Brotli), requests 默认不带 brotli 解压, 加了会返回乱码
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.baidu.com/",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
MIN_TITLE_LEN = 5
# 最多取前 N 条热搜，值来自 settings.crawler_fetch_top_count
FETCH_TOP_COUNT = getattr(settings, 'crawler_fetch_top_count', 20)
# 解析新闻聚合页时最多抓 N 个候选链接，值来自 settings.crawler_news_resolve_candidates
# (用户约定 2026-07-21: 百度搜索页前半部分常被百度自家 (百家号/好看视频)
# 占据, 真新闻 (央视/中华网/网易/新浪) 经常排在第 30-50 位.
# 之前用 10 漏掉一批, 改 30 后某些娱乐话题还是漏, 最终调到 60 覆盖所有)
_NEWS_RESOLVE_CANDIDATES = getattr(settings, 'crawler_news_resolve_candidates', 60)
_NEWS_RESOLVE_TIMEOUT = 6  # 单次跳转探测的超时
# 权威源白名单: 优先返回这几家的原文链接 (按域名子串匹配).
# 用户约定 (2026-07-21): 只看主流媒体, 不要百度百家号 / 知乎这类个人/自媒体.
# 顺序即优先级 — 拿第一个匹配的域名所在的链接作为 ``news_url`` 返回.
# (2026-07-21 追加网易 163.com 作为可信任新闻源)
# (2026-07-21 追加光明网 / 中华网 / 台海网)
_PREFERRED_DOMAINS = (
    # 人民网
    "people.com.cn", "people.cn",
    # 新华网
    "news.cn", "xinhuanet.com",
    # 光明网
    "gmw.cn", "gmw.com.cn",
    # 腾讯
    "qq.com",
    # 新浪
    "sina.com.cn", "sina.cn",
    # 中华网 (news.china.com / china.com / ent.china.com / auto.china.com)
    "china.com",
    # 台海网 (www.taihainet.com)
    "taihainet.com",
    # 观察者网
    "guancha.cn",
    # 网易 (news.163.com / www.163.com / 3g.163.com 等所有 *.163.com)
    "163.com",
)

class BaiduHotCrawler:
    def __init__(self, api_url: str = DEFAULT_API_URL) -> None:
        self.api_url = api_url
        self.session = requests.Session()
        # 合并默认头 (UA + 浏览器特征). User-Agent 单独 set,
        # 其余用 update, 调用方可以再覆盖 (比如代理调试时改 User-Agent).
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.headers["User-Agent"] = USER_AGENT

    def crawl(self) -> List[Dict]:
        """爬取百度热搜。失败行为由 ``baidu_hot_use_mock`` 决定。"""
        try:
            response = self.session.get(self.api_url, timeout=10)
            response.raise_for_status()
            response.encoding = "utf-8"
            # 解析接口JSON
            resp_data = json.loads(response.text)
        except requests.RequestException as exc:
            logger.warning("百度热搜API请求失败: %s", exc)
            return self._on_failure("network error")
        except json.JSONDecodeError as exc:
            logger.warning("百度热搜API返回非标准JSON: %s", exc)
            return self._on_failure("json parse error")

        # 校验接口返回成功标识
        if not resp_data.get("success", False):
            err_msg = resp_data.get("error", {}).get("message", "unknown api error")
            logger.warning(f"百度热搜API业务返回失败: {err_msg}")
            return self._on_failure("api logic fail")

        # 提取热搜列表
        try:
            hot_list = resp_data["data"]["cards"][0]["content"]
        except (KeyError, IndexError) as exc:
            logger.warning("API返回JSON结构变更，未读取到热搜列表: %s", exc)
            return self._on_failure("data structure changed")

        topics: List[Dict] = []
        for item in hot_list[:FETCH_TOP_COUNT]:
            title = item.get("word", "").strip()
            heat_score = int(item.get("hotScore", 0))
            # 区别两个 URL:
            # - rawUrl / url / appUrl: 百度热搜聚合页 (https://www.baidu.com/s?wd=xxx)
            # - 百度热搜 API 现在返回的这三个字段是同一个普通搜索页 URL,
            #   不会带 sa=fyb_news; 需要我们手动拼接切到「新闻」视图,
            #   才能拿到 h3 下的 baidu.com/link?url=... 跳转链.
            source_url = (item.get("rawUrl") or "").strip() or self.api_url
            baidu_news_url = (item.get("url") or "").strip()
            # 强制补上 sa=fyb_news & rsv_dl=fyb_news, 切到新闻聚合视图
            if baidu_news_url and "sa=fyb_news" not in baidu_news_url:
                sep = "&" if "?" in baidu_news_url else "?"
                baidu_news_url = f"{baidu_news_url}{sep}sa=fyb_news&rsv_dl=fyb_news"

            if not title or len(title) < MIN_TITLE_LEN:
                continue

            # 提取话题配图 (百度热搜列表项通常有 img 字段存缩略图)
            img_url = (item.get("img") or "").strip()

            # 把 sa=fyb_news 聚合页解析成真正的详情页 (百家号 / mbd.baidu.com 等)
            # 失败时回退到 baidu_news_url, 还失败就用 rawUrl (热搜页)
            news_url = self._resolve_original_news_url(baidu_news_url)
            if not news_url:
                news_url = baidu_news_url
            if not news_url:
                news_url = source_url

            topics.append({
                "title": title,
                "heat_score": heat_score,
                "source_url": source_url,
                "news_url": news_url,
                "img_url": img_url,
            })

        if not topics:
            logger.warning("API解析到 0 条热搜")
            return self._on_failure("empty parse")

        logger.info("爬取到 %d 条热点", len(topics))
        return topics

    def _on_failure(self, reason: str) -> List[Dict]:
        """统一处理爬取失败。"""
        if settings.baidu_hot_use_mock:
            logger.warning(
                "回退到 mock 数据 (%s)；建议排查真实数据源",
                reason,
            )
            return self._get_mock_data()
        logger.warning(
            "爬取失败 (%s)，返回空列表；不返回 mock 以免污染生产数据",
            reason,
        )
        return []

    @staticmethod
    def _get_mock_data() -> List[Dict]:
        return [
            {"title": "人工智能在教育领域的应用创新", "heat_score": 985000,
             "source_url": "", "news_url": "", "img_url": ""},
            {"title": "新能源汽车市场格局变化分析", "heat_score": 876000,
             "source_url": "", "news_url": "", "img_url": ""},
            {"title": "数字经济时代下的就业趋势",   "heat_score": 765000,
             "source_url": "", "news_url": "", "img_url": ""},
        ]

    # ------------------------------------------------------------------ #
    # 新闻原文 URL 解析: baidu news 聚合页 → 真正可点的详情页
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_real_news_url(url: str) -> bool:
        """判定一个 URL 是不是"主流媒体的新闻原文页".

        用户约定 (2026-07-21):
        - 接受: 任何新闻/媒体网站 (人民网/新华网/腾讯/新浪/网易/观察者网/CCTV/中华网...)
        - 拒绝: 百度自家 (聚合/搜索/captcha) + 个人媒体 (百家号/小红书/抖音/知乎/B站)
        """
        if not url:
            return False
        host = (urlparse(url).netloc or "").lower()
        # 显式拒绝: 百度自家非详情页 + 个人媒体 (用户约定 2026-07-21)
        if any(
            deny in host for deny in (
                # 百度自家
                "www.baidu.com",
                "baidu.com",
                "passport.baidu.com",
                "wappass.baidu.com",
                "baijiahao.baidu.com",      # 百家号
                "mbd.baidu.com",            # 百家号移动详情
                "haokan.baidu.com",         # 好看视频
                # 个人媒体 (用户约定)
                "zhihu.com",                # 知乎问答
                "zhuanlan.zhihu.com",       # 知乎专栏
                "douyin.com",               # 抖音
                "iesdouyin.com",            # 抖音短链
                "xiaohongshu.com",          # 小红书
                "bilibili.com",             # B站
            )
        ):
            return False
        # 排除协议不对或 host 空的情况
        scheme = (urlparse(url).scheme or "").lower()
        if scheme not in ("http", "https"):
            return False
        if not host:
            return False
        # 其余都算真新闻 (央视/中华网/优酷/爱奇艺等媒体或视频站也接受)
        return True

    @staticmethod
    def _is_preferred_source(url: str) -> bool:
        """权威源白名单: 新浪/网易/新华网/人民日报."""
        if not url:
            return False
        host = (urlparse(url).netloc or "").lower()
        return any(d in host for d in _PREFERRED_DOMAINS)

    def _extract_candidate_links(self, html: str) -> List[str]:
        """从百度新闻聚合页 HTML 抽出 ``baidu.com/link?url=...`` 候选链接.

        优先 h3 (新闻标题) 下的 a 标签, 其次正文区. 用正则避免依赖
        BeautifulSoup, 部署轻一点.
        """
        # 先抓 h3 标签里的 (新闻列表常用结构)
        h3_blocks = re.findall(r"<h3[^>]*>(.*?)</h3>", html, flags=re.DOTALL)
        candidates: List[str] = []
        seen = set()
        for block in h3_blocks[:20]:
            for href in re.findall(r'href=["\\\']([^"\\\']+)["\\\']', block):
                if "baidu.com/link?url=" in href and href not in seen:
                    candidates.append(href)
                    seen.add(href)
        # h3 没命中再全页扫一遍
        if not candidates:
            for href in re.findall(
                r'href=["\\\']([^"\\\']*baidu\.com/link\?url=[^"\\\']+)["\\\']',
                html,
            ):
                if href not in seen:
                    candidates.append(href)
                    seen.add(href)
        return candidates[:_NEWS_RESOLVE_CANDIDATES]

    def _follow_baidu_link(self, link_url: str) -> Optional[str]:
        """追踪 ``baidu.com/link?url=...`` 的 302 跳转, 返回真实终 URL.

        关键: 只看第一跳 (Location header). 因为百度第二跳大概率是 captcha
        (wappass.baidu.com), 跟过去就被拒.
        """
        try:
            resp = self.session.head(
                link_url,
                allow_redirects=False,
                timeout=_NEWS_RESOLVE_TIMEOUT,
            )
            if resp.status_code >= 400:
                # 401/405/... fallback 再用 GET 试一次
                resp = self.session.get(
                    link_url,
                    allow_redirects=False,
                    timeout=_NEWS_RESOLVE_TIMEOUT,
                )
                if resp.status_code >= 400:
                    return None
            # 302/301 → Location 头里就是真实详情页
            if resp.status_code in (301, 302, 303, 307, 308):
                real = resp.headers.get("Location", "") or resp.headers.get("location", "")
                return real.strip() or None
            # 没跳转就直接当终 URL
            return resp.url or None
        except requests.RequestException as exc:
            logger.debug("跟随百度链接失败: %s err=%s", link_url[:80], exc)
            return None

    def _resolve_original_news_url(self, baidu_news_url: str) -> str:
        """把 ``&sa=fyb_news`` 聚合页跳转成可点的新闻详情页 URL.

        优先选择权威源 (新浪/网易/新华网/人民日报); 都没有再退化到任何
        真实详情页 (百家号/外站); 一个都跳不通再退回 baidu_news_url.
        """
        if not baidu_news_url:
            return ""
        try:
            resp = self.session.get(
                baidu_news_url,
                timeout=_NEWS_RESOLVE_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.debug(
                "拉取百度新闻聚合页失败: url=%s err=%s",
                baidu_news_url[:80], exc,
            )
            return baidu_news_url

        candidates = self._extract_candidate_links(resp.text)
        # 顺序遍历: 首选权威源, 次选任意真实详情页
        first_valid: Optional[str] = None
        preferred: Optional[str] = None
        for link in candidates:
            real = self._follow_baidu_link(link)
            if not real or not self._is_real_news_url(real):
                continue
            # 第一个能用的真实 URL 记下来做兜底
            if first_valid is None:
                first_valid = real
            if self._is_preferred_source(real):
                preferred = real
                break  # 命中权威源, 立即返回
        if preferred:
            logger.info(
                "新闻原文 URL 解析成功 (权威源): %s -> %s",
                baidu_news_url[:60], preferred[:80],
            )
            return preferred
        if first_valid:
            logger.info(
                "新闻原文 URL 解析成功: %s -> %s",
                baidu_news_url[:60], first_valid[:80],
            )
            return first_valid
        # 一个都没解析成功 (可能被 captcha / 反爬挡了) → 保留原聚合页
        logger.debug(
            "未解析到新闻原文 URL, 保留 baidu 聚合页: %s",
            baidu_news_url[:80],
        )
        return baidu_news_url


if __name__ == "__main__":
    # 配置日志控制台输出，方便本地调试
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    print("===== 百度热搜爬虫测试 =====")
    crawler = BaiduHotCrawler()
    hot_data = crawler.crawl()

    if not hot_data:
        print("未获取到热搜数据")
    else:
        print(f"\n共获取 {len(hot_data)} 条热搜：")
        for idx, item in enumerate(hot_data, 1):
            print(f"{idx:2d}. 热度:{item['heat_score']:>8} | 标题:{item['title']}")

