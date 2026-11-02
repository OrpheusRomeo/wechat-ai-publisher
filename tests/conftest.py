"""Pytest 全局 fixtures.

仅做轻量的路径/salt 配置, 不要在这里 import ``app.*`` (可能触发 settings
实例化, 在没有 .env 的 CI 环境会失败).
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
