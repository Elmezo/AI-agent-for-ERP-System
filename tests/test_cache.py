"""Tests for the TTL cache."""

from __future__ import annotations

import asyncio

from src.cache.memory_cache import TTLCache


async def test_set_get_hit_miss() -> None:
    cache = TTLCache(ttl_seconds=60)
    found, _ = await cache.get("k")
    assert not found and cache.stats()["misses"] == 1
    await cache.set("k", 123)
    found, value = await cache.get("k")
    assert found and value == 123 and cache.stats()["hits"] == 1


async def test_expiry() -> None:
    cache = TTLCache(ttl_seconds=0.01)
    await cache.set("k", "v")
    await asyncio.sleep(0.02)
    found, _ = await cache.get("k")
    assert not found


async def test_eviction_when_over_capacity() -> None:
    cache = TTLCache(ttl_seconds=60, max_entries=2)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)  # evicts "a" (oldest)
    found_a, _ = await cache.get("a")
    found_c, _ = await cache.get("c")
    assert not found_a and found_c


async def test_get_or_set_calls_factory_once() -> None:
    cache = TTLCache(ttl_seconds=60)
    calls = {"n": 0}

    async def factory() -> str:
        calls["n"] += 1
        return "computed"

    first = await cache.get_or_set("k", factory)
    second = await cache.get_or_set("k", factory)
    assert first == second == "computed"
    assert calls["n"] == 1
