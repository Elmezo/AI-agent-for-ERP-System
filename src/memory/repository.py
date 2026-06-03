"""Long-term memory abstraction.

Defines the storage-agnostic interface plus the ``MemoryRecord`` model. The
agent depends only on the :class:`MemoryRepository` protocol, so the SQLite
implementation used today can later be replaced by a PostgreSQL/pgvector one
without touching node logic (per the architecture rules).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now (testable seam)."""
    return datetime.now(timezone.utc)


class MemoryRecord(BaseModel):
    """A single long-term memory item."""

    thread_id: str
    content: str
    kind: str = "fact"  # fact | preference | profile | insight
    importance: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    id: int | None = None


@runtime_checkable
class MemoryRepository(Protocol):
    """Persistence interface for long-term memories."""

    async def initialize(self) -> None:
        """Create the backing store / schema if needed."""
        ...

    async def add(self, memory: MemoryRecord) -> MemoryRecord:
        """Persist a memory and return it with its assigned id."""
        ...

    async def search(self, thread_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Return memories relevant to ``query`` for a conversation thread."""
        ...

    async def recent(self, thread_id: str, limit: int = 5) -> list[MemoryRecord]:
        """Return the most recent memories for a conversation thread."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...
