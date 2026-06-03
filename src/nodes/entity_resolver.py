"""Entity Resolver node.

Turns named entities into concrete ids by executing the plan's ``search`` steps.
For "who owns System ABC?", this resolves "System ABC" -> system id, which later
steps (get_by_id / concept) depend on.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, StepKind
from src.models.state import AgentState
from src.nodes._helpers import append_errors, append_trace
from src.observability.logging import get_logger

_log = get_logger("node.entity_resolver")


class EntityResolverNode:
    """Resolve ``search`` steps into ``{step_id: {facet, id, label, record}}``."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Execute every search step and record the best match per step."""
        plan = ExecutionPlan(**state["plan"])
        start = time.perf_counter()
        resolved: dict[str, Any] = {}
        errors: list[str] = []

        for step in plan.steps:
            if step.kind is not StepKind.SEARCH or not step.facet:
                continue
            result = await self._deps.facets.search(step.facet, step.query or "")
            if result.is_error:
                errors.append(f"search '{step.query}' in {step.facet}: {result.error}")
                resolved[str(step.id)] = {"facet": step.facet, "id": None, "label": None, "record": None}
                continue
            if result.is_empty or not isinstance(result.data, list) or not result.data:
                resolved[str(step.id)] = {
                    "facet": step.facet, "id": None, "label": None, "record": None, "empty": True,
                }
                continue
            record = result.data[0]
            facet_def = self._deps.registry.get_facet(step.facet)
            pk = facet_def.primary_key if facet_def else "id"
            resolved[str(step.id)] = {
                "facet": step.facet,
                "id": record.get(pk),
                "label": self._deps.facets.display_name(step.facet, record),
                "record": record,
            }

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("entities_resolved", count=len(resolved))
        update: dict[str, Any] = {
            "resolved_entities": resolved,
            "trace": append_trace(state, "entity_resolver", elapsed, f"resolved={len(resolved)}"),
        }
        if errors:
            update["errors"] = append_errors(state, errors)
        return update
