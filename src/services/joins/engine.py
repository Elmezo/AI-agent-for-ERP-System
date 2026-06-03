"""Join engine: selects and runs a join strategy.

Callers depend on this engine, not on concrete algorithms. The engine owns a
registry of strategies keyed by name and a configurable default, so adding a new
algorithm is a one-line ``register`` call.
"""

from __future__ import annotations

from typing import Any

from src.models.plan import JoinSpec, JoinType
from src.observability.logging import get_logger
from src.services.joins.base import JoinedRow, JoinStrategy
from src.services.joins.nested_loop import NestedLoopJoin

_log = get_logger("join_engine")


class JoinEngine:
    """Runs joins via a pluggable, named set of strategies."""

    def __init__(
        self,
        strategies: dict[str, JoinStrategy] | None = None,
        default: str = NestedLoopJoin.name,
    ) -> None:
        self._strategies: dict[str, JoinStrategy] = strategies or {}
        self._default = default

    def register(self, strategy: JoinStrategy) -> None:
        """Add or replace a strategy under its ``name``."""
        self._strategies[strategy.name] = strategy

    def join(
        self,
        left: list[dict[str, Any]],
        right: list[dict[str, Any]],
        spec: JoinSpec,
    ) -> list[JoinedRow]:
        """Join ``left`` and ``right`` per ``spec`` using the chosen strategy."""
        name = spec.strategy or self._default
        strategy = self._strategies.get(name)
        if strategy is None:  # fall back to the default rather than failing hard
            _log.warning("join_strategy_unknown_using_default", requested=name, default=self._default)
            strategy = self._strategies[self._default]
        rows = strategy.join(left, right, spec.left_key, spec.right_key, spec.how)
        _log.info(
            "join_done", strategy=strategy.name, how=spec.how.value,
            left=len(left), right=len(right), pairs=len(rows),
        )
        return rows


def build_default_engine() -> JoinEngine:
    """Construct the engine with the v1 strategy set (nested-loop only)."""
    engine = JoinEngine(default=NestedLoopJoin.name)
    engine.register(NestedLoopJoin())
    return engine
