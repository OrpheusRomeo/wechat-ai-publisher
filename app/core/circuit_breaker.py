# app/core/circuit_breaker.py
"""Redis-based circuit breaker for external service calls.

Three states:
  CLOSED  → normal operation; failures increment counter, threshold reached → OPEN
  OPEN    → fail fast; after recovery_timeout seconds → HALF_OPEN
  HALF_OPEN → allow 1 probe request through; success → CLOSED, failure → OPEN

State and failure count are stored in Redis with TTL so the circuit breaker
works correctly across multiple Celery workers / Kafka consumer instances.
"""
import logging
import time
from enum import Enum
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Redis-backed circuit breaker."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._redis = self._get_redis()

    def _get_redis(self):
        """Lazy import to avoid circular dependency at module load."""
        from app.core.redis_client import redis_client
        return redis_client

    # ---- Redis key helpers ----

    def _state_key(self) -> str:
        return f"circuit_breaker:{self.name}:state"

    def _failures_key(self) -> str:
        return f"circuit_breaker:{self.name}:failures"

    def _last_failure_key(self) -> str:
        return f"circuit_breaker:{self.name}:last_failure"

    def _half_open_calls_key(self) -> str:
        return f"circuit_breaker:{self.name}:half_open_calls"

    # ---- State queries ----

    def current_state(self) -> CircuitState:
        """Read current circuit state from Redis (no write)."""
        state = self._redis.run_sync(self._redis.get, self._state_key())
        if state is None:
            return CircuitState.CLOSED
        try:
            return CircuitState(state)
        except ValueError:
            return CircuitState.CLOSED

    def _get_failures(self) -> int:
        raw = self._redis.run_sync(self._redis.get, self._failures_key())
        return int(raw) if raw else 0

    # ---- State transitions ----

    def _set_state(self, state: CircuitState) -> None:
        ttl = self.recovery_timeout if state == CircuitState.OPEN else 86400
        self._redis.run_sync(self._redis.set, self._state_key(), state.value, expire=ttl)

    def _record_success(self) -> None:
        """On success in CLOSED or HALF_OPEN: decrement/reset failure count."""
        self._redis.run_sync(self._redis.delete, self._failures_key())
        if self.current_state() == CircuitState.HALF_OPEN:
            self._redis.run_sync(self._redis.delete, self._state_key())
            self._redis.run_sync(self._redis.delete, self._half_open_calls_key())
            logger.info("Circuit breaker [%s] CLOSED (probe succeeded)", self.name)

    def _record_failure(self) -> None:
        """On failure: increment counter, maybe open circuit."""
        failures = self._get_failures() + 1
        self._redis.run_sync(
            self._redis.set, self._failures_key(), str(failures), expire=86400,
        )
        self._redis.run_sync(
            self._redis.set, self._last_failure_key(), str(int(time.time())), expire=86400,
        )

        state = self.current_state()
        if state == CircuitState.HALF_OPEN:
            # Any failure in half-open → immediately open
            self._set_state(CircuitState.OPEN)
            self._redis.run_sync(self._redis.delete, self._half_open_calls_key())
            logger.warning(
                "Circuit breaker [%s] OPEN (half-open probe failed after %d attempts)",
                self.name, failures,
            )
        elif failures >= self.failure_threshold:
            self._set_state(CircuitState.OPEN)
            logger.warning(
                "Circuit breaker [%s] OPEN after %d failures",
                self.name, failures,
            )

    def _try_half_open(self) -> bool:
        """In OPEN state: check if recovery timeout has passed and transition to HALF_OPEN."""
        last_raw = self._redis.run_sync(self._redis.get, self._last_failure_key())
        if last_raw is None:
            # No last failure recorded → treat as ready to try
            self._set_state(CircuitState.HALF_OPEN)
            self._redis.run_sync(self._redis.set, self._half_open_calls_key(), "0")
            logger.info("Circuit breaker [%s] HALF_OPEN (first probe)", self.name)
            return True
        last_failure = int(last_raw)
        if time.time() - last_failure < self.recovery_timeout:
            return False
        self._set_state(CircuitState.HALF_OPEN)
        self._redis.run_sync(self._redis.set, self._half_open_calls_key(), "0")
        logger.info("Circuit breaker [%s] HALF_OPEN after %.0fs recovery timeout",
                    self.name, time.time() - last_failure)
        return True

    def _allow_request(self) -> bool:
        """Check if a request is allowed. Must be called before making the external call."""
        state = self.current_state()
        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            if not self._try_half_open():
                return False
            # fell through to HALF_OPEN

        # Now HALF_OPEN: allow up to half_open_max_calls probe requests
        raw = self._redis.run_sync(self._redis.get, self._half_open_calls_key())
        current = int(raw) if raw else 0
        if current >= self.half_open_max_calls:
            return False
        self._redis.run_sync(
            self._redis.set, self._half_open_calls_key(), str(current + 1),
        )
        return True

    # ---- Public API ----

    def call(self, func, *args, fallback=None, **kwargs):
        """Execute func if circuit allows, otherwise return fallback.

        Usage:
            result = cb.call(wechat.create_draft, title, content)
            # or with default fallback:
            result = cb.call(some_fn, arg, fallback={"errcode": -1, "errmsg": "circuit open"})
        """
        if not self._allow_request():
            logger.warning(
                "Circuit breaker [%s] OPEN — failing fast: %s",
                self.name, func.__name__,
            )
            if fallback is not None:
                return fallback
            raise CircuitOpenError(f"Circuit breaker [{self.name}] is OPEN")

        try:
            result = func(*args, **kwargs)
            # Only treat None / exception-return as failure if the caller
            # treats them as failure. Here we treat any return as success
            # (the caller decides what to do with None).
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            logger.error(
                "Circuit breaker [%s] recorded failure (%s): %s",
                self.name, func.__name__, exc,
            )
            if fallback is not None:
                return fallback
            raise

    def is_open(self) -> bool:
        """True if circuit is currently OPEN (not allowing requests)."""
        state = self.current_state()
        if state == CircuitState.OPEN:
            # Re-check recovery timeout
            if self._try_half_open():
                return False
            return True
        return False


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is OPEN and no fallback was provided."""
    pass


# ---------------------------------------------------------------------------
# Pre-configured circuit breakers for each external service
# ---------------------------------------------------------------------------

def deepseek_cb() -> CircuitBreaker:
    return CircuitBreaker(
        name="deepseek",
        failure_threshold=getattr(settings, "cb_deepseek_threshold", 5),
        recovery_timeout=getattr(settings, "cb_deepseek_recovery", 60),
    )


def wechat_cb() -> CircuitBreaker:
    return CircuitBreaker(
        name="wechat",
        failure_threshold=getattr(settings, "cb_wechat_threshold", 5),
        recovery_timeout=getattr(settings, "cb_wechat_recovery", 60),
    )


def crawler_cb() -> CircuitBreaker:
    return CircuitBreaker(
        name="crawler",
        failure_threshold=getattr(settings, "cb_crawler_threshold", 5),
        recovery_timeout=getattr(settings, "cb_crawler_recovery", 30),
    )
