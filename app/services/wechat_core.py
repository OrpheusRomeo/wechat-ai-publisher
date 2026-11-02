# app/services/wechat_core.py
"""微信公众平台 API 的**唯一实现**（与凭证来源无关）。

历史背景
--------
本项目曾同时存在两份微信实现：

- ``app/services/wechat.py`` —— 凭据来自 ``.env``
- ``app/services/platforms/wechat_service.py`` —— 凭据来自 ``platform_accounts`` 表

两者把同一套 HTTP 语义各写了一遍，且行为并不一致：熔断器、IP 白名单诊断、
封面本地路径支持、默认封面兜底、配额错误处理都只有其中一份有，导致
「同一个公众号，走不同入口发布结果不同」。

本模块把微信 HTTP 语义收敛成唯一实现，两个入口都退化成薄适配器：

- ``app/services/wechat.py``（``.env`` 凭据）
- ``app/services/platforms/wechat_service.py``（``platform_accounts`` 凭据）

订阅号约束（区别于服务号）见 CLAUDE.md「订阅号约束」一节。
"""
import hashlib
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

import redis
import requests

from app.core.circuit_breaker import CircuitOpenError, wechat_cb
from app.core.config import settings
from app.utils.markdown import markdown_to_html

logger = logging.getLogger(__name__)

# token 失效类错误 → 强制刷新 token 后重试一次
TOKEN_INVALID_ERRCODES = {40001, 42001, 40014, 40125}

# TTL 剩余 < 60s 主动 refresh（不依赖业务调用出错才换 token）
TOKEN_REFRESH_LEEWAY_SECONDS = 60

# 硬配额错误, 遇到后**不重试**, 把 errcode 透传给调用方
# 45009: reach max api daily quota (个人订阅号 1 篇/天触发)
# 45004: user 24h publish limit (认证订阅号 / 服务号 24h 内已发满)
QUOTA_ERRCODES = {45009, 45004}

# IP 白名单错误 40101 (gateway ip forbidden)
# 部署后需将服务器公网 IP 添加到微信公众平台后台 → 设置与开发 → 基本配置 → IP 白名单
IP_WHITELIST_ERRCODES = {40101}

# 兼容旧引用名（app.tasks.article_tasks 依赖 ``_QUOTA_ERRCODES``）
_QUOTA_ERRCODES = QUOTA_ERRCODES

# 微信素材大小上限 2MB
_MAX_IMAGE_BYTES = 2 * 1024 * 1024


def is_ip_whitelist_error(errcode: int) -> bool:
    """判断是否为 IP 白名单错误 (40101)。"""
    return errcode in IP_WHITELIST_ERRCODES


def log_ip_whitelist_error(api_name: str, result: Dict[str, Any]) -> None:
    """记录 IP 白名单错误并给出解决建议。

    解决方式: 登录微信公众平台 → 设置与开发 → 基本配置 → IP 白名单，
    添加本服务器公网 IP（可在微信后台「接口调用次数查询」查看最近一次调用失败的 IP）。
    """
    logger.error(
        "微信 API [%s] 触发 IP 白名单限制 (errcode=%s): %s\n"
        "请登录微信公众平台后台，将服务器公网 IP 加入白名单后重试。\n"
        "路径: 设置与开发 → 基本配置 → IP 白名单",
        api_name, result.get("errcode"), result.get("errmsg", ""),
    )


# 模块级同步 Redis 客户端（独立连接池，不绑 event loop）
# 用于 access_token / 素材 media_id 缓存。
# 刻意不用 ``redis_client.run_sync``：async handler 里 run_sync 有死锁风险。
_redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=settings.redis_db,
    password=settings.redis_password or None,
    decode_responses=True,
    max_connections=10,
)


class WeChatApiClient:
    """微信公众平台 API 客户端（全同步）。

    实例本身无状态（access_token 走 Redis 缓存），可以安全地在
    Celery worker / Kafka consumer / FastAPI 同步路径中创建。
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        api_url: Optional[str] = None,
        timeout: Optional[int] = None,
        default_cover_url: Optional[str] = None,
        scope: Optional[str] = None,
        redis_conn=None,
    ) -> None:
        self.app_id = app_id or ""
        self.app_secret = app_secret or ""
        self.api_url = api_url or settings.wechat_api_url
        self.timeout = int(timeout or settings.wechat_timeout)
        # 默认封面兜底：显式传入优先，否则回退到全局配置
        self.default_cover_url = (
            default_cover_url
            if default_cover_url is not None
            else getattr(settings, "wechat_default_cover_url", "") or ""
        )
        # token / 素材 media_id 都必须按公众号隔离：
        # 不同 appid 的 access_token 与 media_id 互不通用。
        self.scope = scope or self.app_id or "default"
        self._redis = redis_conn if redis_conn is not None else _redis_client

    # ------------------------------------------------------------------
    # Redis key（按 scope 隔离）
    # ------------------------------------------------------------------
    @property
    def _token_cache_key(self) -> str:
        return f"wechat:access_token:{self.scope}"

    @property
    def _default_cover_cache_key(self) -> str:
        return f"wechat:default_cover_media_id:{self.scope}"

    def _cover_cache_key(self, img_url: str) -> str:
        url_hash = hashlib.sha1(img_url.encode("utf-8")).hexdigest()[:16]
        return f"wechat:cover_media_id:{self.scope}:{url_hash}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _post_json(self, url: str, payload: dict) -> dict:
        """POST JSON，``ensure_ascii=False`` 保证中文不被转成 \\uXXXX。

        微信公众平台部分接口对 \\uXXXX 转义解码有缺陷（草稿箱会直接
        显示原始转义符），所以传原生 UTF-8 字节。
        """
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        response = requests.post(url, data=body, headers=headers, timeout=self.timeout)
        return response.json()

    # ------------------------------------------------------------------
    # access_token
    # ------------------------------------------------------------------
    def get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """获取 ``access_token``，优先走 Redis 缓存。

        修复 M7: TTL 剩余 < 60s 时主动 refresh, 不依赖业务调用出错才换 token。
        """
        cache_key = self._token_cache_key
        if not force_refresh:
            cached = self._redis.get(cache_key)
            if cached:
                # 读取 TTL, 临近过期则视为失效, 主动 refresh
                ttl = self._redis.ttl(cache_key)
                if ttl is None or ttl > TOKEN_REFRESH_LEEWAY_SECONDS:
                    return cached if isinstance(cached, str) else None
                logger.info("access_token 临近过期 (ttl=%s), 主动刷新", ttl)

        url = f"{self.api_url}/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }
        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("获取 access_token 异常: %s", exc)
            return None

        if "access_token" not in result:
            logger.error("获取 access_token 失败: %s", result)
            return None

        token = result["access_token"]
        expires = int(result.get("expires_in", 7200)) - 300
        self._redis.set(cache_key, token, ex=expires)
        return token

    # ------------------------------------------------------------------
    # 客服消息
    # ------------------------------------------------------------------
    def send_customer_message(self, openid: str, content: str) -> bool:
        """发送客服消息（带熔断器保护，OPEN 时快速返回 False）。

        - 订阅号 (个人 / 认证) 都没有模板消息权限, 只能用客服消息
        - 不需要 ``template_id``, 只需 ``touser`` + ``msgtype=text``
        - 用户必须 48h 内与公众号有过交互 (关注 / 发消息 / 点击菜单),
          否则 errcode=45015
        - 修复 M7: ``errcode in {40001, 42001, 40014, 40125}`` 自动刷 token 重试一次.
        """
        def _do() -> bool:
            for attempt in range(2):
                token = self.get_access_token()
                if not token:
                    return False

                url = (
                    f"{self.api_url}/cgi-bin/message/custom/send"
                    f"?access_token={token}"
                )
                payload = {
                    "touser": openid,
                    "msgtype": "text",
                    "text": {"content": content},
                }
                try:
                    result = self._post_json(url, payload)
                except (requests.RequestException, ValueError) as exc:
                    logger.error("发送客服消息异常: %s", exc)
                    return False

                errcode = result.get("errcode")
                if errcode == 0:
                    return True

                # token 失效 → refresh 再试一次
                if errcode in TOKEN_INVALID_ERRCODES and attempt == 0:
                    logger.warning(
                        "客服消息 errcode=%s, 强制刷新 token 后重试: %s",
                        errcode, result,
                    )
                    self.get_access_token(force_refresh=True)
                    continue

                # 48001: api unauthorized — 未认证订阅号没有客服消息权限
                if errcode == 48001:
                    logger.info(
                        "客服消息无权限 (errcode=48001, 公众号未认证), 跳过: openid=%s",
                        openid[:8] + "...",
                    )
                    return False

                if is_ip_whitelist_error(errcode):
                    log_ip_whitelist_error("send_custom_message", result)
                    return False

                logger.error("发送客服消息失败: %s", result)
                return False
            return False

        try:
            return wechat_cb().call(_do, fallback=False)
        except CircuitOpenError:
            logger.warning("WeChat 熔断器 OPEN，客服消息跳过 openid=%s", openid[:8] + "...")
            return False

    # ------------------------------------------------------------------
    # 草稿
    # ------------------------------------------------------------------
    @staticmethod
    def _markdown_to_html(content: str) -> str:
        """Markdown → HTML 转换（使用共享模块）。"""
        return markdown_to_html(content)

    @staticmethod
    def _truncate_to_chars(text: str, max_chars: int) -> str:
        """按字符数截断, 用于微信 title 字段 (硬限制 17 字符)."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    @staticmethod
    def _truncate_to_bytes(text: str, max_bytes: int) -> str:
        """按 UTF-8 字节数截断, 用于微信 author 字段 (硬限制 16 字节).
        不会截到半个汉字.
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
        while len((truncated + "...").encode("utf-8")) > max_bytes:
            truncated = truncated[:-1]
        return truncated + "..."

    def create_draft(
        self,
        title: str,
        content: str,
        cover_url: Optional[str] = None,
        author: str = "AI编辑部",
        digest: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建草稿（带熔断器保护，OPEN 时快速返回 None）。"""
        def _do() -> Optional[Dict[str, Any]]:
            # 闭包内对 digest/content 的赋值会让 Python 认为它们是局部变量，
            # 在赋值前读取会报 UnboundLocalError。故在函数最开头读取外层参数值。
            _outer_digest = digest
            _outer_content = content
            token = self.get_access_token()
            if not token:
                return None

            if cover_url:
                thumb_media_id = self._upload_cover_from_url(cover_url)
            else:
                thumb_media_id = self._get_or_upload_default_cover()
            if not thumb_media_id:
                logger.error(
                    "创建草稿失败: 无法获取封面 thumb_media_id "
                    "(请检查 wechat 默认封面图能否下载, 或手动上传素材到公众号后台)"
                )
                return None

            url = f"{self.api_url}/cgi-bin/draft/add?access_token={token}"
            html_content = self._markdown_to_html(_outer_content)
            _digest_val = _outer_digest if _outer_digest else _outer_content[:100] + ("..." if len(_outer_content) > 100 else "")
            payload = {
                "articles": [{
                    "title": self._truncate_to_chars(title, max_chars=17),
                    "content": html_content,
                    "author": self._truncate_to_bytes(author, max_bytes=8),
                    "thumb_media_id": thumb_media_id,
                    "show_cover_pic": 1,
                    "digest": _digest_val,
                }]
            }
            try:
                result = self._post_json(url, payload)
            except (requests.RequestException, ValueError) as exc:
                logger.error("创建草稿异常: %s", exc)
                return None

            if "media_id" in result:
                logger.info("创建草稿成功: %s", result["media_id"])
                return result

            errcode = result.get("errcode", -1)
            if is_ip_whitelist_error(errcode):
                log_ip_whitelist_error("create_draft", result)
                return None

            logger.error("创建草稿失败: %s", result)
            return None

        try:
            return wechat_cb().call(_do, fallback=None)
        except CircuitOpenError:
            logger.warning("WeChat 熔断器 OPEN，草稿创建跳过")
            return None

    # ------------------------------------------------------------------
    # 素材（封面）
    # ------------------------------------------------------------------
    def _get_or_upload_default_cover(self) -> Optional[str]:
        """获取默认封面图的永久素材 media_id.

        流程:
        1. 查 Redis ``wechat:default_cover_media_id:{scope}`` (永久缓存, 不设 TTL)
        2. 缓存 miss → 从默认封面 URL 下载一张图
        3. 调 ``material/add_material`` 上传为永久素材 (type=image)
        4. 把返回的 media_id 写回 Redis, 后续复用
        5. 任何一步失败返回 None, create_draft 据此放弃整次发布

        为什么是永久素材:
        - 临时素材 (media/upload) 3 天过期, 不能用
        - 永久素材 quota 5000 张, 个人订阅号够用一辈子
        """
        cache_key = self._default_cover_cache_key
        cached = self._redis.get(cache_key)
        if cached:
            return cached if isinstance(cached, str) else None

        cover_url = self.default_cover_url or ""
        if not cover_url:
            logger.error(
                "未配置默认封面图 URL (WECHAT_DEFAULT_COVER_URL), 无法获取默认封面"
            )
            return None

        image_bytes = self._download_image(cover_url, is_default=True)
        if image_bytes is None:
            return None

        media_id = self._upload_material(image_bytes)
        if not media_id:
            logger.error("上传默认封面素材失败")
            return None

        # 3) 永久缓存 (永久素材 media_id 不会过期, 不设 TTL)
        self._redis.set(cache_key, media_id)
        logger.info(
            "默认封面已上传为永久素材: media_id=%s bytes=%d",
            media_id, len(image_bytes),
        )
        return media_id

    def _upload_cover_from_url(self, img_url: str) -> Optional[str]:
        """把外部图片 URL 或本地文件上传为微信永久素材, 返回 media_id.

        支持两种来源:
        - 远程 URL (http:// / https://): 用 requests 下载
        - 本地文件路径: 直接读取文件字节

        按 (公众号, URL) 取 SHA1 后的 hex 做 Redis 缓存 key, 同图只上传一次.
        失败返回 None, 调用方应回退到默认封面.
        """
        cache_key = self._cover_cache_key(img_url)
        cached = self._redis.get(cache_key)
        if cached:
            return cached if isinstance(cached, str) else None

        image_bytes = self._download_image(img_url, is_default=False)
        if image_bytes is None:
            return None

        media_id = self._upload_material(image_bytes)
        if not media_id:
            logger.warning("上传封面素材失败, 回退到默认")
            return None

        # 缓存 (永久素材, 不设 TTL)
        self._redis.set(cache_key, media_id)
        logger.info("封面已上传为永久素材: media_id=%s url=%s", media_id, img_url[:80])
        return media_id

    def _download_image(self, src: str, is_default: bool) -> Optional[bytes]:
        """下载（或读取）图片字节，统一做大小校验。"""
        try:
            if src.startswith(("http://", "https://")):
                resp = requests.get(src, timeout=self.timeout)
                resp.raise_for_status()
                image_bytes = resp.content
            else:
                # 本地文件路径（fetch_and_crop_cover 的产物）
                with open(src, "rb") as f:
                    image_bytes = f.read()
        except requests.RequestException as exc:
            if is_default:
                logger.error("下载默认封面图失败: url=%s err=%s", src, exc)
            else:
                logger.warning("下载封面图失败 url=%s err=%s, 回退到默认", src, exc)
            return None
        except OSError as exc:
            logger.warning("读取本地封面图失败 path=%s err=%s, 回退到默认", src, exc)
            return None

        if len(image_bytes) > _MAX_IMAGE_BYTES:
            if is_default:
                logger.error("默认封面图过大 (>2MB), 微信拒收: %d bytes", len(image_bytes))
            else:
                logger.warning("封面图过大 (>2MB), 回退到默认: %d bytes", len(image_bytes))
            return None

        return image_bytes

    def _upload_material(self, image_bytes: bytes) -> Optional[str]:
        """把图片字节上传为永久素材，返回 media_id。"""
        token = self.get_access_token()
        if not token:
            return None
        upload_url = (
            f"{self.api_url}/cgi-bin/material/add_material"
            f"?access_token={token}&type=image"
        )
        # requests 直接传 (filename, bytes, mime) 在 multipart 里有时会触发
        # 40113 unsupported file type, 即便 content-type 是 image/jpeg.
        # 改成"先落临时文件, 再用 file handle 上传"——和 curl -F 行为一致, 微信稳收.
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                resp = requests.post(
                    upload_url,
                    files={"media": ("cover.jpg", f, "image/jpeg")},
                    timeout=self.timeout,
                )
            result = resp.json()
        except (requests.RequestException, ValueError, OSError) as exc:
            logger.warning("上传封面素材异常: %s, 回退到默认", exc)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        media_id = result.get("media_id")
        if not media_id:
            logger.warning("上传封面素材失败, 回退到默认: %s", result)
            return None
        return media_id

    # ------------------------------------------------------------------
    # 群发
    # ------------------------------------------------------------------
    def publish_draft(self, media_id: str) -> Dict[str, Any]:
        """发布草稿（带熔断器保护，OPEN 时快速返回 errcode=-1）。

        返回原始微信响应 dict，至少包含 ``errcode``；成功时含 ``publish_id``。
        """
        def _do() -> Dict[str, Any]:
            for attempt in range(2):
                token = self.get_access_token()
                if not token:
                    return {"errcode": -1, "errmsg": "access_token unavailable"}

                url = f"{self.api_url}/cgi-bin/freepublish/submit?access_token={token}"
                try:
                    result = self._post_json(url, {"media_id": media_id})
                except (requests.RequestException, ValueError) as exc:
                    logger.error("发布异常: %s", exc)
                    return {"errcode": -1, "errmsg": f"exception: {exc}"}

                errcode = result.get("errcode", -1)
                if errcode == 0:
                    logger.info("发布成功: %s", result)
                    return result

                if errcode in QUOTA_ERRCODES:
                    logger.warning(
                        "发布触发每日配额 errcode=%s, 不再重试: %s",
                        errcode, result,
                    )
                    return result

                if errcode in TOKEN_INVALID_ERRCODES and attempt == 0:
                    logger.warning(
                        "发布 errcode=%s, 强制刷新 token 后重试: %s",
                        errcode, result,
                    )
                    self.get_access_token(force_refresh=True)
                    continue

                if is_ip_whitelist_error(errcode):
                    log_ip_whitelist_error("publish_draft", result)
                    return result

                logger.error("发布失败: %s", result)
                return result
            return {"errcode": -1, "errmsg": "max attempts exhausted"}

        try:
            return wechat_cb().call(
                _do,
                fallback={"errcode": -1, "errmsg": "circuit breaker open"},
            )
        except CircuitOpenError:
            logger.warning("WeChat 熔断器 OPEN，草稿发布跳过 media_id=%s", media_id)
            return {"errcode": -1, "errmsg": "circuit breaker open"}
