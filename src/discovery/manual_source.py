"""Manual registry discovery source.

Treats an existing ``api_registry.json``-shaped file as the source of truth.
Useful for hand-curated registries or for validating/normalising a file in
place (load it and write it back through the generator).
"""

from __future__ import annotations

from src.config.registry import load_endpoints
from src.discovery.base import ApiSource
from src.models.api import ApiEndpoint


class ManualSource(ApiSource):
    """Load endpoints from a manual ``api_registry.json``-style file."""

    name = "manual"

    def load(self) -> list[ApiEndpoint]:
        """Parse the manual registry into endpoints."""
        return list(load_endpoints(self.path).values())
