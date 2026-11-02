# app/services/llm_clients/base.py
"""LLM 客户端抽象基类 + 通用熔断器封装"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests

from app.core.circuit_breaker import deepseek_cb, CircuitOpenError

logger = logging.getLogger(__name__)


class LLMClientBase(ABC):
    """LLM 客户端抽象基类，所有 provider 实现均继承此类"""

    def __init__(
        self,
        api_key: str,
        api_url: str,
        model: str,
        temperature: float = 0.7,
        timeout: int = 60,
        extra_config: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.extra_config = extra_config or {}

        self._session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504],
            ),
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @abstractmethod
    def _do_generate(self, prompt: str) -> Optional[Dict]:
        """实际 HTTP 调用，返回 {"title", "content", "cover_description"}"""
        ...

    @abstractmethod
    def _do_brief(self, prompt: str) -> Optional[str]:
        """生成简讯，返回纯文本字符串"""
        ...

    def generate(self, prompt: str) -> Optional[Dict]:
        """生成文章（带熔断器保护）"""
        try:
            return deepseek_cb().call(self._do_generate, prompt)
        except CircuitOpenError:
            logger.warning("%s 熔断器 OPEN，生成跳过", self.__class__.__name__)
            return None

    def generate_brief(self, prompt: str) -> str:
        """生成简讯（带熔断器保护）"""
        try:
            result = deepseek_cb().call(self._do_brief, prompt)
            return result or ""
        except CircuitOpenError:
            logger.warning("%s 熔断器 OPEN，简讯生成跳过", self.__class__.__name__)
            return ""

    def _parse_response(self, content: str) -> Dict:
        """宽松 JSON 解析，提取第一对花括号"""
        import json
        # 先移除思考标签（如 MiniMax 等模型的 think 标签）
        content = self._strip_think_tags(content)
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end != -1:
                return json.loads(content[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

        # 降级处理
        lines = content.strip().split("\n")
        return {
            "title": lines[0].strip() if lines else "分析文章",
            "content": "\n".join(lines[1:]) if len(lines) > 1 else content,
            "cover_description": "新闻评论配图",
        }

    @staticmethod
    def _strip_think_tags(content: str) -> str:
        """移除 <think>...</think> 等思考标签内容，避免干扰 JSON 解析"""
        import re
        # 移除 <think>...</think> 标签对
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', content)
        # 移除 ```think...``` 代码块
        cleaned = re.sub(r'```think[\s\S]*?```', '', cleaned)
        return cleaned

    def _build_article_prompt(self, topic: str) -> str:
        return f"""
请根据以下话题，撰写一篇适合微信公众号发布的分析文章。

【话题】：{topic}

【写作要求】：
1. 文章结构必须包含以下四个部分：
   - **事件起因**：分析事件发生的背景
   - **发展经过**：梳理关键节点和各方反应
   - **解决方案**：总结当前应对措施
   - **回顾与展望**：预测未来走向

2. 写作风格：
   - 标题控制在25字以内
   - 逻辑清晰，段落分明
   - 字数控制在1500-2000字

请返回以下JSON格式：
{{
    "title": "文章标题",
    "content": "Markdown格式的正文内容",
    "cover_description": "建议的封面图描述"
}}
"""

    def _build_keyword_prompt(
        self, keyword: str, context: str = "", style: str = "standard"
    ) -> str:
        style_guide = {
            "standard": "字数控制在 1500-2000 字，段落分明，逻辑清晰。",
            "detailed": "字数控制在 3000-4000 字，旁征博引，深入分析每个维度。",
            "brief": "字数控制在 800-1200 字，简洁有力，抓住核心要点。",
        }.get(style, "字数控制在 1500-2000 字，段落分明，逻辑清晰。")

        context_block = f"\n【参考资料（来自搜索结果）】\n{context}\n\n" if context else ""

        return f"""
请围绕以下关键词，撰写一篇适合微信公众号发布的深度分析文章。

【关键词】：{keyword}
{context_block}
【结构要求】（四个固定段落, 缺哪段视作不完整）
1. **事件起因**：关键词背后的背景、导火索、核心矛盾
2. **发展经过**：梳理关键节点、各方反应、数据支撑
3. **解决方案**：当前已有的应对 / 处置 / 行业最佳实践
4. **回顾与展望**：趋势预测 + 对读者的启发或建议

【写作风格】
- 标题 ≤ 25 字，需要体现关键词
- {style_guide}
- 当出现数字、人物、机构时尽量引用上下文里的事实，不要编造
- 文章应该有新闻评论的深度和权威感

请返回以下 JSON 格式：
{{
    "title": "文章标题",
    "content": "Markdown 格式的正文内容",
    "cover_description": "建议的封面图描述"
}}
"""

    def _post_chat(self, messages: list, max_tokens: int = 3000) -> Optional[Dict]:
        """通用的 chat/completions POST 调用"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        response = self._session.post(
            f"{self.api_url}/chat/completions",
            headers=headers,
            json=data,
            timeout=self.timeout,
        )
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            logger.error("%s API错误: %s", self.__class__.__name__, response.text)
            return None

    def generate_article(
        self, topic: str, custom_prompt: Optional[str] = None
    ) -> Optional[Dict]:
        prompt = custom_prompt or self._build_article_prompt(topic)
        return self.generate(prompt)

    def generate_article_by_keyword(
        self,
        keyword: str,
        context: str = "",
        custom_prompt: Optional[str] = None,
        style: str = "standard",
    ) -> Optional[Dict]:
        prompt = custom_prompt or self._build_keyword_prompt(keyword, context, style)
        max_tokens = self.extra_config.get("max_tokens", 3000)
        if style == "detailed":
            max_tokens = min(max_tokens * 2, 8000)

        def _do():
            content = self._post_chat(
                [
                    {
                        "role": "system",
                        "content": "你是一位资深新闻评论员，擅长围绕指定关键词撰写深度分析文章。",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            if content:
                return self._parse_response(content)
            return None

        try:
            return deepseek_cb().call(_do)
        except CircuitOpenError:
            logger.warning(
                "%s 熔断器 OPEN，关键词文章生成跳过 keyword=%s",
                self.__class__.__name__,
                keyword,
            )
            return None
