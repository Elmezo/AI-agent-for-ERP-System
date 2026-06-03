"""Analytics node ("SQL mode").

Evaluates the plan's ``aggregate`` steps over the rows produced by their source
``list`` step(s). The heavy lifting lives in :class:`AnalyticsService`; this node
only wires the plan to the resolved data and records typed results.

It runs after the Relationship Resolver so group/filter fields can reference
readable names (e.g. ``orgUnit = "Finance Department"``) rather than raw ids.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, PlanStep, StepKind
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.observability.logging import get_logger

_log = get_logger("node.analytics")


class AnalyticsNode:
    """Compute ``AnalyticsResult`` objects for every aggregate step."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Produce ``analytics`` from aggregate steps, or pass through if none."""
        plan = ExecutionPlan(**state["plan"])
        aggregate_steps = [s for s in plan.steps if s.kind is StepKind.AGGREGATE and s.aggregate]
        if not aggregate_steps:
            return {}  # nothing to do; keep the turn untouched

        start = time.perf_counter()
        resolved = state.get("resolved_results") or state.get("execution_results", [])
        rows_by_step = self._index_list_rows(resolved)

        analytics: list[dict[str, Any]] = []
        for step in aggregate_steps:
            facet = step.facet or ""
            rows = self._source_rows(step, facet, rows_by_step, resolved)
            label_field = self._label_field(facet)
            result = self._deps.analytics.aggregate(
                facet, rows, step.aggregate, label_field=label_field
            )
            analytics.append(result.model_dump(mode="json"))

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("analytics_done", computed=len(analytics))
        return {
            "analytics": analytics,
            "trace": append_trace(state, "analytics", elapsed, f"computed={len(analytics)}"),
        }

    # --- source resolution --------------------------------------------------
    @staticmethod
    def _index_list_rows(resolved: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        """Map each successful list step_id to its list of rows."""
        out: dict[int, list[dict[str, Any]]] = {}
        for entry in resolved:
            step_id = entry.get("step_id")
            result = entry.get("result") or {}
            data = result.get("data")
            if step_id is not None and result.get("status") == "success" and isinstance(data, list):
                out[int(step_id)] = [r for r in data if isinstance(r, dict)]
        return out

    def _source_rows(
        self,
        step: PlanStep,
        facet: str,
        rows_by_step: dict[int, list[dict[str, Any]]],
        resolved: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Find the rows this aggregate operates on.

        Prefers an explicit ``depends_on`` list step; otherwise falls back to any
        successful list result for the same facet.
        """
        for dep in step.depends_on:
            if dep in rows_by_step:
                return rows_by_step[dep]
        for entry in resolved:
            result = entry.get("result") or {}
            data = result.get("data")
            if entry.get("facet") == facet and isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
        return []

    def _label_field(self, facet: str) -> str:
        """Display field used to label individual rows in a top-N ranking."""
        facet_def = self._deps.registry.get_facet(facet)
        if facet_def and facet_def.display_fields:
            return facet_def.display_fields[0]
        return "name"
