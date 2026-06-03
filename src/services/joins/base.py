"""Join strategy contract.

Defines the abstraction every join algorithm implements. Keeping this minimal
and algorithm-agnostic is what lets the engine swap ``NestedLoopJoin`` for a
``HashJoin``/``SortMergeJoin`` later with zero changes to callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from src.models.plan import JoinType


@dataclass(frozen=True)
class JoinedRow:
    """One matched pair from a join.

    ``right`` is ``None`` for unmatched left rows in a LEFT join. Carrying both
    sides (rather than a pre-merged dict) keeps projection decisions with the
    caller, so the same strategy serves inner/left/semi joins.
    """

    left: dict[str, Any]
    right: dict[str, Any] | None


def keys_match(left_value: Any, right_value: Any) -> bool:
    """Compare two join keys, coercing numbers so ``7`` matches ``"7"``.

    Shared by all strategies so equality semantics never diverge between
    algorithms.
    """
    if left_value is None or right_value is None:
        return False
    if left_value == right_value:
        return True
    ln, rn = _as_number(left_value), _as_number(right_value)
    if ln is not None and rn is not None:
        return ln == rn
    return str(left_value).strip().lower() == str(right_value).strip().lower()


def _as_number(value: Any) -> float | None:
    """Coerce to float when sensible (bools excluded), else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


class JoinStrategy(ABC):
    """Joins two record sets on a key and returns matched pairs."""

    #: Stable name used to register/select the strategy.
    name: ClassVar[str] = "abstract"

    @abstractmethod
    def join(
        self,
        left: list[dict[str, Any]],
        right: list[dict[str, Any]],
        left_key: str,
        right_key: str,
        how: JoinType,
    ) -> list[JoinedRow]:
        """Return the joined pairs of ``left`` and ``right`` on the given keys."""
        raise NotImplementedError
