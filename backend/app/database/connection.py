"""
Async connection pools for PostgreSQL (via asyncpg) and Redis (via redis.asyncio).
Both pools are initialised once at app startup and closed on shutdown.
"""
import logging
from typing import AsyncGenerator

import asyncpg
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("recoverai.db")

# Module-level pool references (set by init_db / close_db)
_pg_pool: asyncpg.Pool | None = None
_redis_pool: aioredis.Redis | None = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create connection pools. Call once at app startup."""
    global _pg_pool, _redis_pool

    logger.info("Initialising PostgreSQL pool …")
    _pg_pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("PostgreSQL pool ready")

    logger.info("Initialising Redis pool …")
    _redis_pool = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    await _redis_pool.ping()
    logger.info("Redis pool ready")


async def close_db() -> None:
    """Close connection pools. Call once at app shutdown."""
    global _pg_pool, _redis_pool

    if _pg_pool:
        await _pg_pool.close()
        logger.info("PostgreSQL pool closed")

    if _redis_pool:
        await _redis_pool.aclose()
        logger.info("Redis pool closed")


# ── Dependency helpers ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI dependency — yields a single connection from the pool."""
    if _pg_pool is None:
        raise RuntimeError("PostgreSQL pool not initialised")
    async with _pg_pool.acquire() as conn:
        yield conn


def get_redis() -> aioredis.Redis:
    """Return the shared Redis client (not a dependency, just a getter)."""
    if _redis_pool is None:
        raise RuntimeError("Redis pool not initialised")
    return _redis_pool
