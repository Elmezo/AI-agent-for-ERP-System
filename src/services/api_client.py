"""Configurable API client.

Sits between the agent nodes and the transport adapter. Responsibilities:

  * resolve a logical API name to its :class:`ApiEndpoint` via the registry
  * apply TTL caching to idempotent (GET) calls
  * never leak raw HTTP responses - always return an :class:`ApiResult`

The client knows nothing about *which* adapter it wraps (mock/real); it is given
an ``ERPAdapter`` instance by the composition root.
"""

from __future__ import annotations

import json
from typing import Any

from src.adapters.base import ERPAdapter
from src.config.registry import Registry
from src.models.api import ApiResult, HttpMethod
from src.observability.logging import get_logger
from src.cache.memory_cache import TTLCache

_log = get_logger("api_client")


class ApiClient:
    """High-level, cached entry point for executing registry endpoints."""

    def __init__(self, registry: Registry, adapter: ERPAdapter, cache: TTLCache) -> None:
        self._registry = registry
        self._adapter = adapter
        self._cache = cache

    async def call(
        self,
        api_name: str,
        *,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        use_cache: bool = True,
    ) -> ApiResult:
        """Execute an API by name, returning a normalised ``ApiResult``.

        Unknown API names yield an error result rather than raising, so the
        pipeline can degrade gracefully and report the problem to the user.
        """
        endpoint = self._registry.get_endpoint(api_name)
        if endpoint is None:
            return ApiResult.failure(api_name, f"unknown API '{api_name}'")

        cacheable = use_cache and endpoint.method is HttpMethod.GET
        cache_key = self._cache_key(api_name, path_params, query_params) if cacheable else None

        if cache_key is not None:
            found, cached = await self._cache.get(cache_key)
            if found:
                _log.debug("cache_hit", api=api_name)
                return cached.model_copy(update={"from_cache": True})

        result = await self._adapter.call(
            endpoint,
            path_params=path_params or {},
            query_params=query_params or {},
            body=body,
        )

        # Only cache deterministic, non-error outcomes.
        if cache_key is not None and not result.is_error:
            await self._cache.set(cache_key, result)

        return result

    @staticmethod
    def _cache_key(
        api_name: str,
        path_params: dict[str, Any] | None,
        query_params: dict[str, Any] | None,
    ) -> str:
        """Build a stable cache key from the call signature."""
        payload = {
            "api": api_name,
            "path": path_params or {},
            "query": query_params or {},
        }
        return json.dumps(payload, sort_keys=True, default=str)
