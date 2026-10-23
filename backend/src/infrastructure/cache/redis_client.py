"""
Redis client — cache, sessions, rate limiting, pub/sub, and event bus.

Design decisions:
- Single Redis connection pool shared across the application
- Typed helper methods for common patterns (get/set/delete/TTL)
- Separate logical databases for different concerns (cache=0, locks=1)
- All keys are namespaced to avoid collisions
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

import redis.asyncio as aioredis

from src.shared.config.settings import get_settings
from src.shared.exceptions.domain import CacheError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Key namespacing
# ---------------------------------------------------------------------------

NAMESPACE = "ata"  # ai-trading-assistant


def make_key(*parts: str) -> str:
    """Create a namespaced Redis key."""
    return f"{NAMESPACE}:{':'.join(parts)}"


# Predefined key patterns
def candle_key(exchange: str, symbol: str, timeframe: str) -> str:
    return make_key("candles", exchange, symbol.replace("/", "-"), timeframe)


def indicator_key(exchange: str, symbol: str, indicator: str, timeframe: str) -> str:
    return make_key("indicators", exchange, symbol.replace("/", "-"), indicator, timeframe)


def order_dedup_key(order_hash: str) -> str:
    return make_key("order_dedup", order_hash)


def portfolio_key(mode: str, exchange: str) -> str:
    return make_key("portfolio", mode, exchange)


def rate_limit_key(identifier: str) -> str:
    return make_key("rate_limit", identifier)


def agent_output_key(symbol: str, agent_type: str) -> str:
    return make_key("agent_output", symbol.replace("/", "-"), agent_type)


# ---------------------------------------------------------------------------
# Redis Client wrapper
# ---------------------------------------------------------------------------


class RedisClient:
    """
    Async Redis client with typed helpers and namespace management.

    Wraps redis.asyncio.Redis with application-specific patterns.
    """

    def __init__(self, client: Redis) -> None:
        self._redis = client

    # -------------------------------------------------------------------------
    # Core operations
    # -------------------------------------------------------------------------

    async def get(self, key: str) -> str | None:
        """Get a string value."""
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes) else value
        except Exception as exc:
            raise CacheError(f"Redis GET failed for key={key!r}: {exc}") from exc

    async def set(self, key: str, value: str | bytes, ttl_seconds: int | None = None) -> None:
        """Set a string value with optional TTL."""
        try:
            if ttl_seconds:
                await self._redis.setex(key, ttl_seconds, value)
            else:
                await self._redis.set(key, value)
        except Exception as exc:
            raise CacheError(f"Redis SET failed for key={key!r}: {exc}") from exc

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys. Returns count deleted."""
        try:
            return await self._redis.delete(*keys)
        except Exception as exc:
            raise CacheError(f"Redis DELETE failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        try:
            return bool(await self._redis.exists(key))
        except Exception as exc:
            raise CacheError(f"Redis EXISTS failed for key={key!r}: {exc}") from exc

    async def expire(self, key: str, seconds: int) -> None:
        """Set TTL on existing key."""
        await self._redis.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        """Get remaining TTL in seconds. -1 = no TTL, -2 = not exists."""
        return await self._redis.ttl(key)

    # -------------------------------------------------------------------------
    # JSON helpers
    # -------------------------------------------------------------------------

    async def get_json(self, key: str) -> Any:
        """Get and deserialize a JSON value."""
        raw = await self.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Serialize and store a JSON value."""
        serialized = json.dumps(value, default=str)
        await self.set(key, serialized, ttl_seconds=ttl_seconds)

    # -------------------------------------------------------------------------
    # Atomic operations (for distributed locking and dedup)
    # -------------------------------------------------------------------------

    async def set_if_not_exists(self, key: str, value: str, ttl_seconds: int) -> bool:
        """
        SET NX EX — set only if key doesn't exist.

        Returns True if the key was set (i.e., didn't exist).
        Used for distributed locks and order dedup.
        """
        try:
            result = await self._redis.set(key, value, nx=True, ex=ttl_seconds)
            return result is not None
        except Exception as exc:
            raise CacheError(f"Redis SETNX failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Order deduplication
    # -------------------------------------------------------------------------

    async def is_duplicate_order(self, order_hash: str, ttl_seconds: int = 60) -> bool:
        """
        Check and mark an order as seen.

        Returns True if this order has already been processed (duplicate).
        Used to prevent double-execution of orders.
        """
        key = order_dedup_key(order_hash)
        # Returns True if already exists (was NOT set → duplicate)
        was_set = await self.set_if_not_exists(key, "1", ttl_seconds=ttl_seconds)
        return not was_set  # If NOT set, it already existed → duplicate

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------

    async def check_rate_limit(
        self, identifier: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """
        Sliding window rate limiter.

        Returns (allowed: bool, remaining: int).
        """
        key = rate_limit_key(identifier)
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        current_count = results[0]
        remaining = max(0, limit - current_count)
        return current_count <= limit, remaining

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    async def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            result = await self._redis.ping()
            return bool(result)
        except Exception:
            return False

    async def close(self) -> None:
        """Close the connection pool."""
        await self._redis.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_redis_client() -> RedisClient:
    """Create a RedisClient from application settings."""
    settings = get_settings()
    client = aioredis.from_url(
        str(settings.redis.url),
        password=settings.redis.password or None,
        max_connections=settings.redis.max_connections,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
    return RedisClient(client)


# Module-level singleton (initialized on app startup)
_redis_client: RedisClient | None = None


async def get_redis() -> RedisClient:
    """FastAPI dependency for Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client()
    return _redis_client
