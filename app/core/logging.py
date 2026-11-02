# app/core/logging.py
"""统一的日志设置。

项目主要使用标准库 ``logging``，但 ``requirements.txt`` 中也引入了 ``loguru``。
此处提供 ``configure_logging``，第一次调用会重置 root handler，后续调用是 no-op，
以便在 ``app/main.py`` 或入口脚本处显式调用一次即可。
"""
import logging
import sys
from typing import Optional

from app.core.config import settings

_configured = False

_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | "
    "%(filename)s:%(lineno)d | %(message)s"
)


def configure_logging(level: Optional[str] = None) -> None:
    global _configured
    if _configured:
        return

    log_level = (level or settings.log_level or "INFO").upper()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # 抑制部分库的冗余日志
    logging.getLogger("kafka").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
