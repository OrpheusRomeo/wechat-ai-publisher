# app/services/llm_clients/__init__.py
"""LLM 客户端工厂"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.llm_provider import LLMProvider

from .deepseek import DeepSeekClient
from .openai import OpenAIClient
from .minimax import MiniMaxClient
from .qwen import QwenClient
from .base import LLMClientBase


def get_llm_client(provider: "LLMProvider") -> LLMClientBase:
    """根据 provider_type 返回对应客户端实例"""
    factory_map = {
        "deepseek": DeepSeekClient,
        "openai": OpenAIClient,
        "minimax": MiniMaxClient,
        "qwen": QwenClient,
    }
    client_cls = factory_map.get(provider.provider_type)
    if not client_cls:
        raise ValueError(f"不支持的 provider_type: {provider.provider_type}")

    return client_cls(
        api_key=provider.api_key_masked,  # caller must supply decrypted key
        api_url=provider.api_url,
        model=provider.model,
        temperature=provider.temperature,
        timeout=provider.timeout,
        extra_config=provider.extra_config or {},
    )


def build_llm_client(
    provider_type: str,
    api_key: str,
    api_url: str,
    model: str,
    temperature: float = 0.7,
    timeout: int = 60,
    extra_config: dict = None,
) -> LLMClientBase:
    """给定配置直接构造客户端（绕过数据库解密）"""
    factory_map = {
        "deepseek": DeepSeekClient,
        "openai": OpenAIClient,
        "minimax": MiniMaxClient,
        "qwen": QwenClient,
    }
    client_cls = factory_map.get(provider_type)
    if not client_cls:
        raise ValueError(f"不支持的 provider_type: {provider_type}")

    return client_cls(
        api_key=api_key,
        api_url=api_url,
        model=model,
        temperature=temperature,
        timeout=timeout,
        extra_config=extra_config or {},
    )
