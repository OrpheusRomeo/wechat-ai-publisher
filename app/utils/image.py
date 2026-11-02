"""图片处理工具：抓取文章封面图、裁剪为微信公众号封面尺寸。

微信公众号封面图推荐尺寸：900×383px（比例约 2.35:1）。
本模块从任意文章 URL 提取封面图并自动裁剪为适配尺寸。
"""
import hashlib
import io
import logging
import os
import re
import tempfile
from typing import Optional

import requests
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

# 微信封面推荐尺寸（2.35:1）
WECHAT_COVER_WIDTH = 900
WECHAT_COVER_HEIGHT = 383

# 图片缓存目录（挂载到 volumes 时可持久化）
_COVER_CACHE_DIR = os.environ.get("COVER_CACHE_DIR", "/tmp/wechat_covers")
os.makedirs(_COVER_CACHE_DIR, exist_ok=True)


def _fetch_html(url: str, timeout: int = 15) -> Optional[str]:
    """获取网页 HTML 内容"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        # 微信文章需要编码处理
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.RequestException as exc:
        logger.warning("抓取文章页面失败: %s -> %s", url, exc)
        return None


def _extract_og_image(html: str) -> Optional[str]:
    """从 HTML 中提取 og:image 封面图 URL"""
    match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1)
    # 兼容另一种写法
    match2 = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.IGNORECASE)
    if match2:
        return match2.group(1)
    return None


def _extract_wx_cover_url(html: str) -> Optional[str]:
    """从微信文章 HTML 中提取封面图 URL（兼容多种格式）"""
    # 格式1: var cover = "https://mmbiz.qpic.cn/..."
    match = re.search(r'var\s+cover\s*=\s*["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)

    # 格式2: "cover":"https://mmbiz.qpic.cn/..."
    match2 = re.search(r'"cover"\s*:\s*"([^"]+)"', html)
    if match2:
        return match2.group(1)

    # 格式3: 找所有 img 标签，取第一个有尺寸的大图
    img_matches = re.findall(
        r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>',
        html,
    )
    for img_url in img_matches:
        # 跳过微信小图、emoji、placeholder
        if any(x in img_url for x in ["qlogo", "表情", "emoji", "data:image"]):
            continue
        # 优先选 mmbiz.qpic.cn（微信媒体库大图）
        if "mmbiz.qpic.cn" in img_url:
            return img_url

    return None


def _download_image(url: str, timeout: int = 15) -> Optional[bytes]:
    """下载图片，返回原始字节"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/",
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        logger.warning("下载封面图失败: %s -> %s", url, exc)
        return None


def _crop_to_wechat_cover(image_bytes: bytes) -> Optional[bytes]:
    """将图片裁剪为微信封面比例（2.35:1），返回裁剪后的 JPEG 字节。

    裁剪策略：
    1. 将图片缩放到宽度=900，保持比例
    2. 如果高度>383，从中间裁剪掉多余部分（center crop）
    3. 如果高度<383，用白色填充（但一般不会出现，原始图高度通常够）
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # 统一转为 RGB（JPEG 不支持 RGBA）
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        orig_w, orig_h = img.size

        # 计算目标尺寸：宽度固定 900，高度按比例
        target_ratio = WECHAT_COVER_WIDTH / WECHAT_COVER_HEIGHT  # ≈ 2.35
        current_ratio = orig_w / orig_h

        if current_ratio > target_ratio:
            # 图片偏宽：缩放宽度到 900，裁剪高度
            new_w = WECHAT_COVER_WIDTH
            new_h = int(round(WECHAT_COVER_WIDTH / current_ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            # 从中间裁剪高度到 383
            top = (new_h - WECHAT_COVER_HEIGHT) // 2
            img = img.crop((0, top, WECHAT_COVER_WIDTH, top + WECHAT_COVER_HEIGHT))
        else:
            # 图片偏窄/正方形：缩放高度到 383，裁剪宽度
            new_h = WECHAT_COVER_HEIGHT
            new_w = int(round(WECHAT_COVER_HEIGHT * current_ratio))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            # 从中间裁剪宽度到 900（不够宽则填充白边）
            if new_w < WECHAT_COVER_WIDTH:
                new_img = Image.new("RGB", (WECHAT_COVER_WIDTH, WECHAT_COVER_HEIGHT), (255, 255, 255))
                left = (WECHAT_COVER_WIDTH - new_w) // 2
                new_img.paste(img, (left, 0))
                img = new_img
            else:
                left = (new_w - WECHAT_COVER_WIDTH) // 2
                img = img.crop((left, 0, left + WECHAT_COVER_WIDTH, WECHAT_COVER_HEIGHT))

        img = img.convert("RGB")
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=90, optimize=True)
        return output.getvalue()

    except Exception as exc:
        logger.error("裁剪封面图失败: %s", exc)
        return None


def _save_cover(image_bytes: bytes) -> str:
    """保存裁剪后的封面图到本地缓存目录，返回文件路径"""
    # 用内容 hash 做文件名，避免重复下载
    file_hash = hashlib.sha256(image_bytes).hexdigest()[:16]
    filename = f"wechat_cover_{file_hash}.jpg"
    filepath = os.path.join(_COVER_CACHE_DIR, filename)
    if not os.path.exists(filepath):
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        logger.info("封面图已缓存: %s", filepath)
    return filepath


def fetch_and_crop_cover(article_url: str) -> Optional[str]:
    """从文章 URL 抓取封面图，自动裁剪为微信封面尺寸，返回本地文件路径。

    Args:
        article_url: 文章页面 URL（支持微信公众号、任意网页）

    Returns:
        本地 JPEG 文件路径（可直接作为 cover_url 使用）
        失败返回 None
    """
    html = _fetch_html(article_url)
    if not html:
        return None

    # 优先提取 og:image
    image_url = _extract_og_image(html)
    if not image_url:
        # 尝试微信专属格式
        image_url = _extract_wx_cover_url(html)

    if not image_url:
        logger.warning("未在页面中找到封面图: %s", article_url)
        return None

    image_bytes = _download_image(image_url)
    if not image_bytes:
        return None

    cropped = _crop_to_wechat_cover(image_bytes)
    if not cropped:
        return None

    return _save_cover(cropped)


def get_cover_url(article_url: str) -> Optional[str]:
    """兼容旧接口：传入文章 URL，返回裁剪后的本地封面图路径。

    在 wechat.create_draft 或 article_publishing 中使用：
        cover_local = fetch_and_crop_cover(source_url)
    """
    return fetch_and_crop_cover(article_url)
