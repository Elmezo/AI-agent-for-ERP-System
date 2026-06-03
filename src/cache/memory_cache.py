"""Async in-memory TTL cache.

Used to deduplicate entity lookups, searches, and relationship resolutions so a
repeated ``people.get_by_id(1)`` hits the backend only once within the TTL
window. Concurrency-safe and bounded (simple FIFO eviction when full).

The interface is deliberately small so it can later be backed by Redis or
another store without changing callers.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class TTLCache:
    """A bounded, time-to-live cache safe for concurrent async use."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 2048) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> tuple[bool, Any]:
        """Return ``(found, value)``; ``found`` is False on miss or expiry."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return False, None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                self.misses += 1
                return False, None
            self._store.move_to_end(key)
            self.hits += 1
            return True, value

    async def set(self, key: str, value: Any) -> None:
        """Store a value with the configured TTL, evicting if over capacity."""
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic() + self._ttl, value)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value or compute, store, and return it.

        ``factory`` is only awaited on a cache miss. Note: results are cached
        unconditionally, so callers should avoid caching error states they want
        to retry (the service layer caches only successful/empty results).
        """
        found, value = await self.get(key)
        if found:
            return value
        computed = await factory()
        await self.set(key, computed)
        return computed

    async def clear(self) -> None:
        """Empty the cache and reset counters."""
        async with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int]:
        """Return hit/miss counters and current size."""
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store)}
