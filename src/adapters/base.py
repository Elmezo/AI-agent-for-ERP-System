"""ERP transport adapter protocol.

The agent depends only on this ``ERPAdapter`` interface. Concrete adapters
(mock / real) are interchangeable and selected by configuration, so swapping the
backend never touches agent logic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.models.api import ApiEndpoint, ApiResult


@runtime_checkable
class ERPAdapter(Protocol):
    """Anything that can execute a registry endpoint and return an ``ApiResult``."""

    async def call(
        self,
        endpoint: ApiEndpoint,
        *,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ApiResult:
        """Execute a single endpoint and return a normalised result."""
        ...

    async def aclose(self) -> None:
        """Release any underlying resources (HTTP connections, etc.)."""
        ...
