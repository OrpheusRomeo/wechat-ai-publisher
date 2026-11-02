# app/api/deps.py
"""FastAPI 共享依赖。

- ``get_pagination`` : 统一解析分页参数
- ``verify_wechat_signature`` : 微信回调签名校验占位
"""
from typing import Optional, Tuple

from fastapi import Header, HTTPException, Query

from app.core.config import settings

# 复用 ``app.core.database.get_db`` (FastAPI Depends 友好的迭代器形式)
from app.core.database import get_db  # noqa: F401  re-exported for routers


class PaginationParams:
    """分页参数封装，可作为依赖注入使用。"""

    def __init__(
        self,
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def total_pages(self, total: int) -> int:
        if self.page_size <= 0:
            return 0
        return (total + self.page_size - 1) // self.page_size


def get_pagination(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


def verify_wechat_signature(
    signature: Optional[str] = Header(default=None, alias="X-WeChat-Signature"),
    timestamp: Optional[str] = Header(default=None, alias="X-WeChat-Timestamp"),
    nonce: Optional[str] = Header(default=None, alias="X-WeChat-Nonce"),
) -> Tuple[str, str, str]:
    """接入层校验微信回调签名占位。接入网关后的真实校验应放在 ``services/wechat.py``。"""
    if settings.app_env != "production":
        return signature or "", timestamp or "", nonce or ""
    if not (signature and timestamp and nonce):
        raise HTTPException(status_code=401, detail="missing wechat signature")
    return signature, timestamp, nonce
