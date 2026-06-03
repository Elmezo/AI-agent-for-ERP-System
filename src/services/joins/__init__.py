"""Pluggable join framework.

A small strategy-pattern engine for joining two record sets on a key. The rest
of the system (planner, analytics, nodes) depends only on :class:`JoinEngine`
and :class:`JoinStrategy`, never on a concrete algorithm. New strategies
(``HashJoin``, ``SortMergeJoin``, ``IndexJoin``) can be registered later without
touching callers.
"""

from __future__ import annotations

from src.services.joins.base import JoinedRow, JoinStrategy
from src.services.joins.engine import JoinEngine, build_default_engine
from src.services.joins.nested_loop import NestedLoopJoin

__all__ = [
    "JoinedRow",
    "JoinStrategy",
    "JoinEngine",
    "build_default_engine",
    "NestedLoopJoin",
]
