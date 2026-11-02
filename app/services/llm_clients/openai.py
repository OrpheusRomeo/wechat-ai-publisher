# app/services/llm_clients/openai.py
"""OpenAI 大模型客户端"""
import logging
from typing import Dict, Optional

from .base import LLMClientBase

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClientBase):
    """OpenAI API 客户端（OpenAI兼容格式）"""

    def _do_generate(self, prompt: str) -> Optional[Dict]:
        content = self._post_chat(
            messages=[
                {
                    "role": "system",
                    "content": "你是一位资深新闻评论员，擅长撰写深度分析文章。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.extra_config.get("max_tokens", 3000),
        )
        if content:
            return self._parse_response(content)
        return None

    def _do_brief(self, prompt: str) -> Optional[str]:
        content = self._post_chat(
            messages=[
                {
                    "role": "system",
                    "content": "你是一位资深新闻评论员，擅长撰写深度分析文章。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
        )
        if not content:
            return None
        content = content.strip().strip('「」"\'\'"').strip()
        content = content.replace("\n", " ")
        if len(content) > 50:
            content = content[:49].rstrip("，,。 ") + "…"
        return content or None
