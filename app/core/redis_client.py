# app/core/redis_client.py
import asyncio
import json
import logging
import threading
from functools import partial
from typing import Any, Awaitable, Callable, Optional, TypeVar

import redis.asyncio as redis_async
from redis.asyncio import ConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RedisClient:
    """全局异步 Redis 客户端。

    使用 ConnectionPool 复用连接，提供 ``get_json`` / ``set_json`` 两个
    JSON 序列化快捷方法，对应 ``app/services/wechat.py`` 与 ``app/main.py`` 中
    调用的接口。
    """

    def __init__(self) -> None:
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[redis_async.Redis] = None
        # 用于 sync 调用的长生命周期 event loop, 避免 asyncio.run() 关闭后
        # redis 客户端仍绑在已关闭 loop 上的问题 (publish_consumer 接入发现)
        self._sync_loop: Optional[asyncio.AbstractEventLoop] = None
        self._sync_loop_lock = threading.Lock()

    async def get_client(self) -> redis_async.Redis:
        if self._client is None:
            await self._connect()
        return self._client  # type: ignore[return-value]

    async def _connect(self) -> None:
        url = (
            f"redis://:{settings.redis_password}@{settings.redis_host}:"
            f"{settings.redis_port}/{settings.redis_db}"
        )
        self._pool = ConnectionPool.from_url(
            url,
            max_connections=20,
            decode_responses=True,
        )
        self._client = redis_async.Redis(connection_pool=self._pool)
        await self._client.ping()
        logger.info("Redis 连接已建立: %s:%s/%s",
                    settings.redis_host, settings.redis_port, settings.redis_db)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None
        logger.info("Redis 连接已关闭")

    # ---- 通用 KV (异步) ----
    async def get(self, key: str) -> Optional[str]:
        client = await self.get_client()
        return await client.get(key)

    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        client = await self.get_client()
        return bool(await client.set(key, value, ex=expire))

    async def set_nx(self, key: str, value: str, expire: int) -> bool:
        """SET key value NX EX ttl — 返回 True 表示设置成功（key 不存在），None 表示 key 已存在（用于 nonce 防重放）。"""
        client = await self.get_client()
        return bool(await client.set(key, value, nx=True, ex=expire))

    async def delete(self, key: str) -> int:
        client = await self.get_client()
        return int(await client.delete(key))

    async def ttl(self, key: str) -> Optional[int]:
        """返回 key 剩余 TTL (秒); key 不存在返回 -2, 无 TTL 返回 -1.

        修复 (publish_consumer 接入后触发): ``wechat.get_access_token`` 用它
        判断 access_token 是否临近过期. 之前 RedisClient 没这个方法, 第二次
        调用 wechat 服务时 AttributeError.
        """
        client = await self.get_client()
        return int(await client.ttl(key))

    # ---- JSON 便捷方法 (供 wechat 服务调用) ----
    async def get_json(self, key: str) -> Any:
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Redis key=%s 不是合法 JSON, 已返回 None", key)
            return None

    async def set_json(self, key: str, value: Any, expire: Optional[int] = None) -> bool:
        return await self.set(key, json.dumps(value, ensure_ascii=False), expire=expire)

    # ---- 同步调用入口 (供非异步上下文使用：WeChatService 等) ----
    def run_sync(self, awaitable_or_callable, *args, **kwargs) -> Any:
        """在同步上下文中执行 async 方法 / 闭包.

        修复 L1: 文档明确各种调用方语义, 并标注高并发场景应换 ``redis.Redis()``.

        - 调用方 = Celery worker (同步 / 无 running loop) → 在专有长生命周期
          loop 上跑 ``run_until_complete`` (不关闭 loop, 让 redis async 客户端
          一直绑定在该 loop 上, 避免 "Event loop is closed")
        - 调用方 = FastAPI sync 路由 (中间件) → 走主 loop 的
          ``run_coroutine_threadsafe`` + ``.result()``, 会阻塞调用线程
        - 调用方 = FastAPI async handler → 仍用 ``run_coroutine_threadsafe``
          但与原 loop 同线程, ``.result()`` 会死锁 — 高风险

        推荐: 高并发 FastAPI 路由直接 ``await redis_client.get(...)``, 不要
        用 ``run_sync``; Celery worker / Kafka consumer 用 ``run_sync`` 安全.

        高风险检测: 如果在 async handler 中调用 run_sync，会记录警告日志。
        """
        if callable(awaitable_or_callable):
            # 兼容三种入参: 同步函数 / 异步函数 / 已构造好的 coroutine
            awaitable_or_callable = awaitable_or_callable(*args, **kwargs)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 不在事件循环里 (Celery worker / Kafka consumer 常见):
            # 用一个持久 loop, 避免 asyncio.run() 关闭后 redis 客户端
            # 失效的问题.
            return self._run_on_sync_loop(awaitable_or_callable)

        # 检测是否在 FastAPI async handler 中调用（高风险场景）
        # run_coroutine_threadsafe + result() 在同 loop 会死锁
        logger.warning(
            "Redis.run_sync 从 async handler 调用存在死锁风险！"
            "请改用 await redis_client.get()/set() 直接调用。"
        )
        # 已在 loop 中：使用 run_coroutine_threadsafe（仍会阻塞，但至少不会卡死 loop）
        return asyncio.run_coroutine_threadsafe(
            awaitable_or_callable, loop
        ).result()

    def _run_on_sync_loop(self, coro) -> Any:
        """在进程级持久 event loop 上跑 coroutine, loop 永不关闭."""
        if self._sync_loop is None or self._sync_loop.is_closed():
            with self._sync_loop_lock:
                if self._sync_loop is None or self._sync_loop.is_closed():
                    self._sync_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._sync_loop)
        return self._sync_loop.run_until_complete(coro)

    # ---- 分布式锁 (供 Celery 任务使用, 防止同一资源被并发处理) ----
    async def acquire_lock(
        self,
        key: str,
        ttl_seconds: int = 60,
        retry_times: int = 0,
        retry_interval: float = 0.2,
    ) -> Optional[str]:
        """尝试获取分布式锁, 返回 token (用于安全释放); 获取失败返回 None。

        使用 ``SET key token NX EX ttl`` 原子操作, 避免 SET-then-EXPIRE 之间的
        死锁窗口。``acquire_lock`` 不重试时语义即 "try-lock", 适合 Celery
        任务在锁已被占时快速跳过。
        """
        import uuid as _uuid
        client = await self.get_client()
        token = _uuid.uuid4().hex
        for _ in range(max(retry_times, 0) + 1):
            ok = await client.set(key, token, nx=True, ex=ttl_seconds)
            if ok:
                return token
            if retry_times <= 0:
                return None
            await asyncio.sleep(retry_interval)
        return None

    async def release_lock(self, key: str, token: str) -> bool:
        """用 Lua 脚本保证只释放自己持有的锁 (防误删别人的锁)。"""
        client = await self.get_client()
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "  return redis.call('del', KEYS[1]) "
            "else return 0 end"
        )
        result = await client.eval(script, 1, key, token)
        return bool(result)

    def acquire_lock_sync(
        self,
        key: str,
        ttl_seconds: int = 60,
        retry_times: int = 0,
        retry_interval: float = 0.2,
    ) -> Optional[str]:
        """同步入口, 供 Celery 任务直接调用。"""
        return self.run_sync(
            self.acquire_lock, key, ttl_seconds, retry_times, retry_interval
        )

    def release_lock_sync(self, key: str, token: str) -> bool:
        """同步入口, 供 Celery 任务直接调用。"""
        return self.run_sync(self.release_lock, key, token)


# 单例，供 ``from app.core.redis_client import redis_client`` 直接使用
redis_client = RedisClient()
