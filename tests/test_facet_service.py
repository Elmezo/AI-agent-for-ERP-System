"""Tests for the API client + facet service over a mocked HTTP backend."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.adapters.factory import build_adapter
from src.cache.memory_cache import TTLCache
from src.config.registry import Registry
from src.config.settings import Settings
from src.services.api_client import ApiClient
from src.services.facet_service import FacetService


def _make_service(settings: Settings, registry: Registry) -> tuple[FacetService, TTLCache, httpx.AsyncClient]:
    client = httpx.AsyncClient(base_url=settings.erp_base_url)
    adapter = build_adapter(settings, client)
    cache = TTLCache(ttl_seconds=60)
    api_client = ApiClient(registry, adapter, cache)
    return FacetService(registry, api_client), cache, client


@respx.mock
async def test_search_returns_results(settings: Settings, registry: Registry) -> None:
    respx.get("http://erp.test/api/people/search").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Ahmed"}])
    )
    service, _, client = _make_service(settings, registry)
    result = await service.search("people", "Ahmed")
    assert result.ok
    assert result.data[0]["name"] == "Ahmed"
    await client.aclose()


@respx.mock
async def test_empty_search_is_empty_not_error(settings: Settings, registry: Registry) -> None:
    respx.get("http://erp.test/api/people/search").mock(return_value=httpx.Response(200, json=[]))
    service, _, client = _make_service(settings, registry)
    result = await service.search("people", "zzz")
    assert result.is_empty and not result.is_error
    await client.aclose()


@respx.mock
async def test_get_by_id_and_cache(settings: Settings, registry: Registry) -> None:
    route = respx.get("http://erp.test/api/people/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Ahmed"})
    )
    service, cache, client = _make_service(settings, registry)
    first = await service.get_by_id("people", 1)
    second = await service.get_by_id("people", 1)
    assert first.ok and second.from_cache
    assert route.call_count == 1  # second call served from cache
    assert cache.stats()["hits"] == 1
    await client.aclose()


@respx.mock
async def test_404_is_empty(settings: Settings, registry: Registry) -> None:
    respx.get("http://erp.test/api/people/999").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    service, _, client = _make_service(settings, registry)
    result = await service.get_by_id("people", 999)
    assert result.is_empty and not result.is_error
    await client.aclose()


@respx.mock
async def test_server_error_is_error(settings: Settings, registry: Registry) -> None:
    respx.get("http://erp.test/api/people/1").mock(return_value=httpx.Response(500))
    service, _, client = _make_service(settings, registry)
    result = await service.get_by_id("people", 1)
    assert result.is_error
    await client.aclose()


def test_display_name(settings: Settings, registry: Registry) -> None:
    service, _, _ = _make_service(settings, registry)
    assert service.display_name("people", {"id": 1, "name": "Ahmed Mohamed"}) == "Ahmed Mohamed"
