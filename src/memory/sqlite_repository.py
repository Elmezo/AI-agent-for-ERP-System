"""SQLite implementation of :class:`MemoryRepository`.

Lightweight, dependency-free long-term memory for development. Search is a
keyword (LIKE) match scored by token overlap - good enough until a vector store
is introduced. The public surface matches the repository protocol exactly, so
swapping in PostgreSQL/pgvector later is a drop-in change.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.memory.repository import MemoryRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'fact',
    importance  REAL NOT NULL DEFAULT 0.5,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_thread ON memories(thread_id);
"""


class SqliteMemoryRepository:
    """File-backed long-term memory store."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the connection and ensure the schema exists."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def add(self, memory: MemoryRecord) -> MemoryRecord:
        """Insert a memory row and return it with its id populated."""
        db = self._require_db()
        cursor = await db.execute(
            """
            INSERT INTO memories (thread_id, content, kind, importance, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory.thread_id,
                memory.content,
                memory.kind,
                memory.importance,
                json.dumps(memory.metadata, ensure_ascii=False),
                memory.created_at.isoformat(),
            ),
        )
        await db.commit()
        return memory.model_copy(update={"id": cursor.lastrowid})

    async def search(self, thread_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Keyword search scored by how many query tokens appear in content."""
        db = self._require_db()
        tokens = [t for t in query.lower().split() if len(t) > 2]
        async with db.execute(
            "SELECT * FROM memories WHERE thread_id = ? ORDER BY created_at DESC",
            (thread_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        scored: list[tuple[int, MemoryRecord]] = []
        for row in rows:
            record = self._row_to_record(row)
            content = record.content.lower()
            score = sum(1 for t in tokens if t in content)
            if score > 0 or not tokens:
                scored.append((score, record))
        scored.sort(key=lambda pair: (pair[0], pair[1].importance), reverse=True)
        return [record for _, record in scored[:limit]]

    async def recent(self, thread_id: str, limit: int = 5) -> list[MemoryRecord]:
        """Return the most recently created memories for the thread."""
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM memories WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # --- helpers ------------------------------------------------------------
    def _require_db(self) -> aiosqlite.Connection:
        """Return the open connection or raise if not initialised."""
        if self._db is None:
            raise RuntimeError("repository not initialized; call initialize() first")
        return self._db

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> MemoryRecord:
        """Map a DB row to a ``MemoryRecord``."""
        return MemoryRecord(
            id=row["id"],
            thread_id=row["thread_id"],
            content=row["content"],
            kind=row["kind"],
            importance=row["importance"],
            metadata=json.loads(row["metadata"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
