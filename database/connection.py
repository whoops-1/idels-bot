from __future__ import annotations

import asyncpg
from config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def get_db() -> asyncpg.Pool:
    """Returns the singleton connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_db() -> None:
    """Closes the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
