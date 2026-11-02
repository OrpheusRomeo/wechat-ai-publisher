# app/utils/markdown.py
"""Markdown → HTML 转换工具（各平台共用）。

主要支持:
- ``\n`` → ``<br>``
- ``**xxx**`` → ``<strong>xxx</strong>``
- 行首 ``## xxx`` → ``<h2>xxx</h2>``
- ``[text](url)`` → ``<a href="url">text</a>``

注意: H2 转换必须**先**于 ``\\n → <br>`` 替换。
"""
import re

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_H2_RE = re.compile(r"(?m)^##\s+(.+)$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")


def markdown_to_html(content: str) -> str:
    """Markdown → HTML 转换（各平台共用实现）。

    H2 转换必须先于 \\n → <br> 替换，避免 H2 正则贪婪匹配整段内容。
    """
    # 1) 先做 H2: 在原文里识别整行 `## xxx`, 替换成 `<h2>xxx</h2>`
    text = _MD_H2_RE.sub(r"<h2>\1</h2>", content)
    # 2) 再做链接: [text](url) → <a href="url">text</a>
    text = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)
    # 3) 再做 bold: **xxx** 同样不跨行, 此时 H2 已经被替换, 不会受影响
    text = _MD_BOLD_RE.sub(r"<strong>\1</strong>", text)
    # 4) 最后把剩下的 \n 换成 <br> (H2 内部的 \n 已在第 1 步被消化)
    text = text.replace("\n", "<br>")
    return text
