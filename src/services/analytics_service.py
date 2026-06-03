"""In-memory analytics ("SQL mode") over record sets.

The ERP is reached through REST APIs, not a database, so analytic questions are
answered by listing records and computing over them here: filter -> group ->
aggregate -> sort -> limit. This service is pure (no I/O) and deterministic, so
numeric answers are exact and trivially unit-tested.

Honesty guard: if a requested field is missing or non-numeric where a number is
required, the result carries an ``error`` instead of a fabricated value, in line
with the "never invent enterprise data" rule.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.models.analytics import AnalyticsGroup, AnalyticsResult
from src.models.plan import AggregateOp, AggregateSpec, FilterClause, FilterOp
from src.observability.logging import get_logger

_log = get_logger("analytics_service")

# Operations that require a numeric ``metric`` field.
_NUMERIC_OPS = {AggregateOp.SUM, AggregateOp.AVG, AggregateOp.MIN, AggregateOp.MAX}


class AnalyticsService:
    """Compute declarative aggregations over a list of record dicts."""

    def aggregate(
        self,
        facet: str,
        rows: list[dict[str, Any]],
        spec: AggregateSpec,
        *,
        label_field: str = "name",
    ) -> AnalyticsResult:
        """Evaluate ``spec`` over ``rows`` and return a typed result.

        ``label_field`` is the human-readable field used to label individual
        rows in a top-N ranking (defaults to ``name``).
        """
        total = len(rows)
        result = AnalyticsResult(
            facet=facet,
            op=spec.op.value,
            metric=spec.metric,
            group_by=spec.group_by,
            total_rows=total,
        )

        # Validate required fields up front so we fail truthfully, not silently.
        error = self._validate(rows, spec)
        if error is not None:
            result.error = error
            _log.info("analytics_rejected", facet=facet, op=spec.op.value, error=error)
            return result

        matched = [r for r in rows if self._matches(r, spec.filters)]
        result.matched_rows = len(matched)

        if spec.group_by:
            result.groups = self._grouped(matched, spec)
        elif self._is_top_n(spec):
            result.groups = self._top_rows(matched, spec, label_field)
        else:
            result.value = self._scalar(matched, spec)

        _log.info(
            "analytics_computed",
            facet=facet, op=spec.op.value, matched=len(matched),
            groups=len(result.groups), value=result.value,
        )
        return result

    @staticmethod
    def _is_top_n(spec: AggregateSpec) -> bool:
        """A top-N ranking of individual rows: limit + metric, no grouping."""
        return spec.group_by is None and spec.metric is not None and bool(spec.limit)

    # --- validation ---------------------------------------------------------
    @staticmethod
    def _validate(rows: list[dict[str, Any]], spec: AggregateSpec) -> str | None:
        """Return an error string if the spec cannot be computed on these rows."""
        if not rows:
            return None  # empty input is valid; yields 0 / no groups
        sample_keys = {k for row in rows for k in row.keys()}

        needs_numeric_metric = spec.op in _NUMERIC_OPS or AnalyticsService._is_top_n(spec)
        if needs_numeric_metric:
            if not spec.metric:
                return f"operation '{spec.op.value}' requires a numeric field"
            if spec.metric not in sample_keys:
                return f"field '{spec.metric}' not found"
            if not any(_is_number(row.get(spec.metric)) for row in rows):
                return f"field '{spec.metric}' is not numeric"

        if spec.group_by and spec.group_by not in sample_keys:
            return f"group-by field '{spec.group_by}' not found"

        for clause in spec.filters:
            if clause.field not in sample_keys:
                return f"filter field '{clause.field}' not found"
        return None

    # --- filtering ----------------------------------------------------------
    @classmethod
    def _matches(cls, row: dict[str, Any], filters: list[FilterClause]) -> bool:
        """True when ``row`` satisfies every filter clause."""
        return all(cls._matches_one(row.get(c.field), c) for c in filters)

    @staticmethod
    def _matches_one(actual: Any, clause: FilterClause) -> bool:
        """Evaluate a single predicate, comparing numerically when possible."""
        op = clause.op
        expected = clause.value

        if op is FilterOp.CONTAINS:
            return str(expected).strip().lower() in str(actual or "").lower()

        # Compare numerically when both sides are numbers, else case-insensitively.
        a_num, e_num = _as_number(actual), _as_number(expected)
        numeric = a_num is not None and e_num is not None
        left = a_num if numeric else str(actual).strip().lower()
        right = e_num if numeric else str(expected).strip().lower()

        if op is FilterOp.EQ:
            return left == right
        if op is FilterOp.NE:
            return left != right
        # Ordered comparisons only make sense numerically.
        if not numeric:
            return False
        if op is FilterOp.GT:
            return a_num > e_num
        if op is FilterOp.GTE:
            return a_num >= e_num
        if op is FilterOp.LT:
            return a_num < e_num
        if op is FilterOp.LTE:
            return a_num <= e_num
        return False

    # --- aggregation --------------------------------------------------------
    @staticmethod
    def _scalar(rows: list[dict[str, Any]], spec: AggregateSpec) -> float | None:
        """Compute a single aggregate value over ``rows``."""
        if spec.op is AggregateOp.COUNT:
            return float(len(rows))
        values = [_as_number(r.get(spec.metric)) for r in rows]
        nums = [v for v in values if v is not None]
        if not nums:
            return None
        if spec.op is AggregateOp.SUM:
            return float(sum(nums))
        if spec.op is AggregateOp.AVG:
            return round(sum(nums) / len(nums), 4)
        if spec.op is AggregateOp.MIN:
            return float(min(nums))
        if spec.op is AggregateOp.MAX:
            return float(max(nums))
        return None

    @classmethod
    def _grouped(
        cls, rows: list[dict[str, Any]], spec: AggregateSpec
    ) -> list[AnalyticsGroup]:
        """Aggregate ``rows`` per ``group_by`` value, then sort and limit.

        When there is no ``group_by`` but a ``limit`` is set with a metric, this
        path is also used to produce a top-N ranking of individual rows.
        """
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = row.get(spec.group_by)
            buckets[str(key) if key is not None else "(none)"].append(row)

        groups = [
            AnalyticsGroup(
                key=key,
                value=cls._scalar(members, spec) or 0.0,
                count=len(members),
            )
            for key, members in buckets.items()
        ]
        groups.sort(key=lambda g: g.value, reverse=spec.sort_desc)
        if spec.limit is not None and spec.limit > 0:
            groups = groups[: spec.limit]
        return groups

    @staticmethod
    def _top_rows(
        rows: list[dict[str, Any]], spec: AggregateSpec, label_field: str
    ) -> list[AnalyticsGroup]:
        """Rank individual rows by ``metric`` and keep the top ``limit``."""
        ranked: list[AnalyticsGroup] = []
        for row in rows:
            value = _as_number(row.get(spec.metric))
            if value is None:
                continue
            label = str(row.get(label_field) or row.get("name") or row.get("id") or "?")
            ranked.append(AnalyticsGroup(key=label, value=value, count=1))
        ranked.sort(key=lambda g: g.value, reverse=spec.sort_desc)
        if spec.limit is not None and spec.limit > 0:
            ranked = ranked[: spec.limit]
        return ranked


def _is_number(value: Any) -> bool:
    """True when ``value`` can be interpreted as a real number (not a bool)."""
    return _as_number(value) is not None


def _as_number(value: Any) -> float | None:
    """Coerce ``value`` to ``float`` when sensible, else ``None``.

    ``bool`` is intentionally rejected so ``True``/``False`` are not summed as
    1/0, and numeric strings like ``"250000"`` are accepted.
    """
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
