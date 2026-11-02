# app/models/llm_provider.py
"""大模型供应商配置表"""
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.core.database import Base


class LLMProvider(Base):
    """大模型供应商配置"""
    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, comment="显示名称，如 DeepSeek 官方")
    provider_type = Column(String(30), nullable=False, index=True, comment="类型标识: deepseek/openai/minimax/qwen")
    api_url = Column(String(255), nullable=False, comment="API Base URL")
    # 加密存储的 API Key
    api_key_encrypted = Column(Text, nullable=False, default="")
    # 掩码显示（如 sk-****abcd）
    api_key_masked = Column(String(64), default="", nullable=False)
    # 模型名，如 deepseek-chat / gpt-4o-mini / qwen-plus
    model = Column(String(100), nullable=False)
    temperature = Column(Float, default=0.7)
    timeout = Column(Integer, default=60, comment="超时秒数")
    # 扩展配置（max_tokens 等），JSONB 灵活存储
    extra_config = Column(JSONB, default=dict)
    # 是否默认供应商（同一时间只能有一个默认）
    is_default = Column(Boolean, default=False, server_default="false", nullable=False)
    # 是否启用
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    # 软删除
    is_deleted = Column(Boolean, default=False, server_default="false", nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('ix_llm_providers_type_active', 'provider_type', 'is_active', 'is_deleted'),
        Index('ix_llm_providers_default', 'is_default', postgresql_where=is_deleted == False),
    )
