"""Runtime tool generation from the API registry.

Instead of hand-writing one function per endpoint, tools are generated at
runtime from ``api_registry.json``. Adding an API to the registry instantly
makes a callable tool available - no code changes. Each tool validates that its
required path parameters are present and delegates execution to the cached
:class:`ApiClient`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config.registry import Registry
from src.models.api import ApiEndpoint, ApiResult
from src.services.api_client import ApiClient


@dataclass
class Tool:
    """A runtime-generated, callable wrapper around one registry endpoint."""

    endpoint: ApiEndpoint
    _client: ApiClient

    @property
    def name(self) -> str:
        """The tool/API name."""
        return self.endpoint.name

    @property
    def description(self) -> str:
        """Human/LLM-friendly description."""
        return self.endpoint.description

    def signature(self) -> dict[str, Any]:
        """A small schema describing the tool's parameters (for prompting)."""
        return {
            "name": self.name,
            "facet": self.endpoint.facet,
            "method": self.endpoint.method.value,
            "path_params": list(self.endpoint.path_params),
            "query_params": list(self.endpoint.query_params),
            "description": self.description,
        }

    async def __call__(self, **params: Any) -> ApiResult:
        """Invoke the endpoint, routing params into path / query buckets."""
        path_params = {k: params[k] for k in self.endpoint.path_params if k in params}
        missing = [k for k in self.endpoint.path_params if k not in path_params]
        if missing:
            return ApiResult.failure(
                self.name, f"missing required path params: {', '.join(missing)}"
            )
        query_keys = set(self.endpoint.query_params)
        query_params = {
            k: v for k, v in params.items()
            if k in query_keys or (not query_keys and k not in self.endpoint.path_params)
        }
        body = {k: params[k] for k in self.endpoint.body_params if k in params} or None
        return await self._client.call(
            self.name,
            path_params=path_params,
            query_params=query_params,
            body=body,
        )


class ToolFactory:
    """Builds and indexes :class:`Tool` objects from the registry."""

    def __init__(self, registry: Registry, client: ApiClient) -> None:
        self._registry = registry
        self._client = client

    def build(self) -> dict[str, Tool]:
        """Return a mapping of ``api_name -> Tool`` for every endpoint."""
        return {
            name: Tool(endpoint=endpoint, _client=self._client)
            for name, endpoint in self._registry.endpoints.items()
        }

    def catalog(self) -> list[dict[str, Any]]:
        """Return tool signatures, useful for LLM prompts / introspection."""
        return [tool.signature() for tool in self.build().values()]
