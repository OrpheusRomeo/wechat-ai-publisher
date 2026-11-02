# app/services/llm_provider.py
"""LLM 供应商管理服务"""
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.llm_provider import LLMProvider
from app.core.security import encrypt_value, decrypt_value, mask_value

logger = logging.getLogger(__name__)


class LLMProviderService:
    """LLM 供应商的 CRUD 操作"""

    def __init__(self, db: Session):
        self.db = db

    def get_providers(
        self, include_deleted: bool = False
    ) -> List[LLMProvider]:
        query = self.db.query(LLMProvider)
        if not include_deleted:
            query = query.filter(LLMProvider.is_deleted == False)
        return query.order_by(LLMProvider.is_default.desc(), LLMProvider.created_at.desc()).all()

    def get_provider(self, id: int) -> Optional[LLMProvider]:
        return (
            self.db.query(LLMProvider)
            .filter(LLMProvider.id == id, LLMProvider.is_deleted == False)
            .first()
        )

    def get_default_provider(self) -> Optional[LLMProvider]:
        return (
            self.db.query(LLMProvider)
            .filter(
                LLMProvider.is_default == True,
                LLMProvider.is_deleted == False,
                LLMProvider.is_active == True,
            )
            .first()
        )

    def create_provider(self, data: dict) -> LLMProvider:
        # 加密并掩码 api_key
        api_key_plain = data.pop("api_key")
        encrypted = encrypt_value(api_key_plain)
        masked = mask_value(api_key_plain)

        # 如果新供应商要设为默认，先清除其他默认
        is_default = data.get("is_default", False)
        if is_default:
            self._clear_default()

        provider = LLMProvider(
            **data,
            api_key_encrypted=encrypted,
            api_key_masked=masked,
        )
        self.db.add(provider)
        self.db.commit()
        self.db.refresh(provider)
        logger.info("创建 LLM 供应商: id=%s name=%s type=%s", provider.id, provider.name, provider.provider_type)
        return provider

    def update_provider(self, id: int, data: dict) -> Optional[LLMProvider]:
        provider = self.get_provider(id)
        if not provider:
            return None

        # 如果设置 is_default=True，先清除其他默认
        if data.get("is_default"):
            self._clear_default()

        # api_key 单独处理（可选更新）
        if "api_key" in data:
            api_key_plain = data.pop("api_key")
            if api_key_plain:
                provider.api_key_encrypted = encrypt_value(api_key_plain)
                provider.api_key_masked = mask_value(api_key_plain)

        for key, value in data.items():
            if key in ("api_key_encrypted", "api_key_masked"):
                continue
            setattr(provider, key, value)

        self.db.commit()
        self.db.refresh(provider)
        logger.info("更新 LLM 供应商: id=%s name=%s", provider.id, provider.name)
        return provider

    def delete_provider(self, id: int) -> bool:
        provider = self.get_provider(id)
        if not provider:
            return False
        provider.is_deleted = True
        self.db.commit()
        logger.info("删除 LLM 供应商: id=%s name=%s", provider.id, provider.name)
        return True

    def set_default(self, id: int) -> Optional[LLMProvider]:
        provider = self.get_provider(id)
        if not provider:
            return None
        self._clear_default()
        provider.is_default = True
        self.db.commit()
        self.db.refresh(provider)
        logger.info("设为默认 LLM 供应商: id=%s name=%s", provider.id, provider.name)
        return provider

    def decrypt_api_key(self, id: int) -> Optional[str]:
        provider = self.get_provider(id)
        if not provider:
            return None
        if not provider.api_key_encrypted:
            return None
        return decrypt_value(provider.api_key_encrypted)

    def _clear_default(self) -> None:
        """清除所有供应商的 is_default"""
        self.db.query(LLMProvider).filter(
            LLMProvider.is_default == True
        ).update({"is_default": False})
