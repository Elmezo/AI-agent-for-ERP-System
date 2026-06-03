"""Facet-level business operations.

Wraps the generic :class:`ApiClient` with facet-aware helpers (search, get by
id, list, readable labels). It uses ``facets.yaml`` to know which endpoint
implements each operation and which field is the primary key, so it stays fully
config-driven.
"""

from __future__ import annotations

from typing import Any

from src.config.registry import Registry
from src.models.api import ApiResult, FacetDef
from src.observability.logging import get_logger
from src.services.api_client import ApiClient

_log = get_logger("facet_service")


class FacetService:
    """Config-driven operations over business facets."""

    def __init__(self, registry: Registry, client: ApiClient) -> None:
        self._registry = registry
        self._client = client

    # --- queries ------------------------------------------------------------
    async def search(self, facet: str, term: str) -> ApiResult:
        """Search a facet using its configured search API."""
        definition = self._require_facet(facet)
        if not definition.search_api:
            return ApiResult.failure(f"{facet}.search", f"facet '{facet}' has no search API")
        return await self._client.call(definition.search_api, query_params={"q": term})

    async def get_by_id(self, facet: str, entity_id: Any) -> ApiResult:
        """Fetch one record of a facet by primary key."""
        definition = self._require_facet(facet)
        if not definition.get_by_id_api:
            return ApiResult.failure(f"{facet}.get_by_id", f"facet '{facet}' has no get_by_id API")
        endpoint = self._registry.require_endpoint(definition.get_by_id_api)
        param_name = endpoint.path_params[0] if endpoint.path_params else definition.primary_key
        return await self._client.call(definition.get_by_id_api, path_params={param_name: entity_id})

    async def list_all(self, facet: str) -> ApiResult:
        """List all records of a facet using its configured list API."""
        definition = self._require_facet(facet)
        if not definition.list_api:
            return ApiResult.failure(f"{facet}.list", f"facet '{facet}' has no list API")
        return await self._client.call(definition.list_api)

    async def call_facet_api(self, api_name: str, entity_id: Any | None = None) -> ApiResult:
        """Call an arbitrary facet endpoint, supplying its id path param if any."""
        endpoint = self._registry.get_endpoint(api_name)
        if endpoint is None:
            return ApiResult.failure(api_name, f"unknown API '{api_name}'")
        path_params: dict[str, Any] = {}
        if endpoint.path_params and entity_id is not None:
            path_params[endpoint.path_params[0]] = entity_id
        return await self._client.call(api_name, path_params=path_params)

    # --- helpers ------------------------------------------------------------
    async def resolve_record(self, facet: str, entity_id: Any) -> dict[str, Any] | None:
        """Return a single record dict for a facet id, or ``None`` if missing."""
        result = await self.get_by_id(facet, entity_id)
        if result.ok and isinstance(result.data, dict):
            return result.data
        return None

    def display_name(self, facet: str, record: dict[str, Any]) -> str:
        """Build a human-readable label for a record using ``display_fields``."""
        definition = self._registry.get_facet(facet)
        if definition is None:
            return str(record.get("name") or record.get("id") or record)
        return definition.display_label(record)

    def _require_facet(self, facet: str) -> FacetDef:
        """Return the facet definition or raise a clear error."""
        return self._registry.require_facet(facet)
