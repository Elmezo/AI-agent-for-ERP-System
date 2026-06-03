"""Typed results of the analytics (SQL-style) engine.

The analytics service computes these from a set of records and an
:class:`~src.models.plan.AggregateSpec`. Keeping the output typed lets the
context builder, response generator, and tests rely on a stable shape instead
of ad-hoc dicts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyticsGroup(BaseModel):
    """One bucket of a grouped/ranked aggregation, e.g. ``IT -> 4``."""

    key: str
    value: float
    # Rows contributing to this group (useful for "count" interpretation).
    count: int = 0


class AnalyticsResult(BaseModel):
    """Outcome of one analytics computation.

    Exactly one of ``value`` (scalar) or ``groups`` (grouped/ranked) is the
    primary answer. ``error`` is set when the request could not be honoured
    (e.g. an unknown or non-numeric field), so the agent reports it truthfully
    instead of inventing a number.
    """

    facet: str
    op: str
    metric: str | None = None
    group_by: str | None = None
    # Rows considered after filtering, and the unfiltered total (for context).
    matched_rows: int = 0
    total_rows: int = 0
    # Scalar answer for ungrouped aggregations (count/sum/avg/min/max).
    value: float | None = None
    # Per-group answers for grouped or top-N aggregations.
    groups: list[AnalyticsGroup] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when a usable answer was produced."""
        return self.error is None and (self.value is not None or bool(self.groups))
