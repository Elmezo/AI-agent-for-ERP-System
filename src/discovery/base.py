"""Discovery source abstraction.

A *discovery source* knows how to read some external description of an API
(OpenAPI, Postman, a manual registry) and emit a normalised list of
``ApiEndpoint`` objects. The generator merges sources into ``api_registry.json``.

Adding support for a new format means implementing one ``ApiSource`` subclass;
no other code changes.
"""

from __future__ import annotations

import abc
from pathlib import Path

from src.models.api import ApiEndpoint


class ApiSource(abc.ABC):
    """Base class for all discovery sources."""

    #: Short identifier used on the CLI (``--source <name>``).
    name: str = "base"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @abc.abstractmethod
    def load(self) -> list[ApiEndpoint]:
        """Parse the source file and return normalised endpoints."""
        raise NotImplementedError

    @staticmethod
    def _facet_from_operation_id(operation_id: str) -> str | None:
        """Derive a facet from a dotted operationId like ``people.search``."""
        if "." in operation_id:
            return operation_id.split(".", 1)[0]
        return None
