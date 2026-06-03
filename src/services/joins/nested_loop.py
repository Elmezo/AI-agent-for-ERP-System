"""Nested-loop join strategy (v1 default).

The simplest correct algorithm: for each left row, scan every right row. O(n*m)
but dependency-free and ideal for the small result sets returned by ERP APIs.
Heavier strategies (hash/sort-merge/index) can replace it transparently for
large datasets.
"""

from __future__ import annotations

from typing import Any

from src.models.plan import JoinType
from src.services.joins.base import JoinedRow, JoinStrategy, keys_match


class NestedLoopJoin(JoinStrategy):
    """Pairs rows by scanning the right side for each left row."""

    name = "nested_loop"

    def join(
        self,
        left: list[dict[str, Any]],
        right: list[dict[str, Any]],
        left_key: str,
        right_key: str,
        how: JoinType,
    ) -> list[JoinedRow]:
        """Return inner/left matches of ``left`` and ``right`` on the keys."""
        out: list[JoinedRow] = []
        for left_row in left:
            left_value = left_row.get(left_key)
            matched = False
            for right_row in right:
                if keys_match(left_value, right_row.get(right_key)):
                    out.append(JoinedRow(left=left_row, right=right_row))
                    matched = True
            if how is JoinType.LEFT and not matched:
                out.append(JoinedRow(left=left_row, right=None))
        return out
