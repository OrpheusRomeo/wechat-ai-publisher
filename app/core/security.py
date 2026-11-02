# app/core/security.py
"""加密工具模块。

- Fernet (AES-128-CBC + HMAC) 对称加密：用于加密 DB 中存储的敏感配置
- 密码哈希：bcrypt
- 掩码显示：显示最后 4 位，其余为 ****
"""
import logging
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

# Fernet 实例（模块级单例，避免重复解析 key）
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.effective_fernet_key)
    return _fernet


def hash_password(password: str) -> str:
    """对密码做 bcrypt 哈希。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码。"""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def encrypt_value(plaintext: str) -> str:
    """加密字符串，返回 url-safe base64 token。"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    """解密 token，返回明文。失败时返回空字符串。"""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("解密失败，fernet key 可能已变更")
        return ""


def mask_value(value: str, visible_chars: int = 4) -> str:
    """掩码显示：首尾各保留 visible_chars，中间用 **** 替代。

    >>> mask_value("wx1234567890abcdef", 4)
    'wx12****cdef'
    >>> mask_value("sk-short", 4)
    'sk-s****'
    """
    if not value:
        return ""
    if len(value) <= visible_chars * 2:
        return value[:visible_chars] + "****"
    return value[:visible_chars] + "****" + value[-visible_chars:]
