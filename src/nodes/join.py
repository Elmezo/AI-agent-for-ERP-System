"""Join node (cross-entity linking).

Evaluates the plan's ``join`` steps, linking the rows of two prior steps on a
key (e.g. ``system.ownerId == project.ownerId``) and surfacing the matched rows
as a synthetic result. The actual matching is delegated to the pluggable
:class:`JoinEngine`, so the algorithm (nested-loop today) can change without
touching this node.

It runs after the Relationship Resolver (so joins can also key on resolved
names) and before the Analytics node (so an ``aggregate`` step can compute over
a join's output by depending on it).
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, JoinSpec, PlanStep, StepKind
from src.models.state import AgentState
from src.nodes._helpers import append_errors, append_trace
from src.observability.logging import get_logger

_log = get_logger("node.join")


class JoinNode:
    """Compute ``join`` steps and append their matched rows to results."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Run each join step, injecting its output as a new result entry."""
        plan = ExecutionPlan(**state["plan"])
        join_steps = [s for s in plan.steps if s.kind is StepKind.JOIN and s.join]
        if not join_steps:
            return {}

        start = time.perf_counter()
        results = list(state.get("resolved_results") or state.get("execution_results", []))
        rows_by_step = self._index_rows(results)
        step_facet = {s.id: s.facet for s in plan.steps}
        errors: list[str] = []

        for step in join_steps:
            spec = step.join
            entry = self._run_join(step, spec, rows_by_step, step_facet, errors)
            results.append(entry)
            # Make the join output available to later joins/aggregates in this turn.
            data = entry["result"].get("data")
            if isinstance(data, list):
                rows_by_step[step.id] = data

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("joins_done", count=len(join_steps))
        update: dict[str, Any] = {
            "resolved_results": results,
            "trace": append_trace(state, "join", elapsed, f"joins={len(join_steps)}"),
        }
        if errors:
            update["errors"] = append_errors(state, errors)
        return update

    # --- helpers ------------------------------------------------------------
    @staticmethod
    def _index_rows(results: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        """Map each successful step_id to its rows (a dict becomes a 1-item list)."""
        out: dict[int, list[dict[str, Any]]] = {}
        for entry in results:
            step_id = entry.get("step_id")
            result = entry.get("result") or {}
            if step_id is None or result.get("status") != "success":
                continue
            data = result.get("data")
            if isinstance(data, list):
                out[int(step_id)] = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, dict):
                out[int(step_id)] = [data]
        return out

    def _run_join(
        self,
        step: PlanStep,
        spec: JoinSpec,
        rows_by_step: dict[int, list[dict[str, Any]]],
        step_facet: dict[int, str | None],
        errors: list[str],
    ) -> dict[str, Any]:
        """Execute one join and build its synthetic result entry."""
        left = rows_by_step.get(spec.left_step, [])
        right = rows_by_step.get(spec.right_step, [])
        emit_facet = step.facet or self._emit_facet(spec, step_facet)

        if not left or not right:
            errors.append(
                f"join step {step.id}: missing rows "
                f"(left_step={spec.left_step}, right_step={spec.right_step})"
            )
            return self._entry(step, emit_facet, "empty", [])

        pairs = self._deps.joins.join(left, right, spec)
        emitted = self._project(pairs, spec.emit)
        status = "success" if emitted else "empty"
        return self._entry(step, emit_facet, status, emitted)

    @staticmethod
    def _emit_facet(spec: JoinSpec, step_facet: dict[int, str | None]) -> str | None:
        """Pick the facet of the emitted side so downstream trimming works."""
        source = spec.left_step if spec.emit == "left" else spec.right_step
        return step_facet.get(source)

    @staticmethod
    def _project(pairs: list[Any], emit: str) -> list[dict[str, Any]]:
        """Turn joined pairs into emitted rows based on ``emit``."""
        rows: list[dict[str, Any]] = []
        for pair in pairs:
            if emit == "left":
                rows.append(pair.left)
            elif emit == "both":
                merged = {**(pair.right or {}), **pair.left}
                rows.append(merged)
            elif pair.right is not None:  # default: emit the right side
                rows.append(pair.right)
        return rows

    @staticmethod
    def _entry(
        step: PlanStep, facet: str | None, status: str, data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build a result entry mirroring the executor's shape."""
        return {
            "step_id": step.id,
            "kind": StepKind.JOIN.value,
            "facet": facet,
            "api_name": "join",
            "focus": None,
            "result": {"api_name": "join", "status": status, "data": data},
        }
