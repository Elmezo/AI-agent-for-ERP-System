"""API Selector node.

Translates the plan's non-search steps into concrete API invocations, consulting
the registry and the semantic catalog. Business concepts are resolved here:

  * a concept backed by an ``api`` (e.g. ``systems.stakeholders``) becomes a call
  * a concept backed by a ``field`` (e.g. ``owner`` -> ``ownerId``) becomes a
    *focus marker* so the Relationship Resolver/Context Builder surface it.

This node performs no execution.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, PlanStep, StepKind
from src.models.state import AgentState
from src.nodes._helpers import append_errors, append_trace
from src.observability.logging import get_logger

_log = get_logger("node.api_selector")


class ApiSelectorNode:
    """Produce ``selected_apis``: a list of concrete calls / focus markers."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Build the list of API calls and concept-focus markers."""
        plan = ExecutionPlan(**state["plan"])
        resolved = state.get("resolved_entities", {})
        start = time.perf_counter()
        selected: list[dict[str, Any]] = []
        errors: list[str] = []

        for step in plan.steps:
            if step.kind is StepKind.SEARCH:
                continue
            try:
                entry = self._select_for_step(step, resolved)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if entry is not None:
                selected.append(entry)

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("apis_selected", count=len(selected))
        update: dict[str, Any] = {
            "selected_apis": selected,
            "trace": append_trace(state, "api_selector", elapsed, f"calls={len(selected)}"),
        }
        if errors:
            update["errors"] = append_errors(state, errors)
        return update

    # --- per-step selection -------------------------------------------------
    def _select_for_step(self, step: PlanStep, resolved: dict[str, Any]) -> dict[str, Any] | None:
        """Return a selection entry for one step (or None to skip)."""
        facet = step.facet
        facet_def = self._deps.registry.get_facet(facet) if facet else None

        if step.kind is StepKind.GET_BY_ID:
            if facet_def is None or not facet_def.get_by_id_api:
                raise ValueError(f"facet '{facet}' has no get_by_id API")
            entity_id = self._resolve_id(step, resolved)
            if entity_id is None:
                return None  # nothing to fetch (entity not found)
            endpoint = self._deps.registry.require_endpoint(facet_def.get_by_id_api)
            param = endpoint.path_params[0] if endpoint.path_params else facet_def.primary_key
            return self._call(step, facet, facet_def.get_by_id_api, path_params={param: entity_id})

        if step.kind is StepKind.LIST:
            if facet_def is None or not facet_def.list_api:
                raise ValueError(f"facet '{facet}' has no list API")
            return self._call(step, facet, facet_def.list_api)

        if step.kind is StepKind.API:
            if not step.action:
                raise ValueError(f"step {step.id} kind=api missing action")
            return self._select_api(step, step.action, resolved)

        if step.kind is StepKind.CONCEPT:
            return self._select_concept(step, resolved)

        return None

    def _select_concept(self, step: PlanStep, resolved: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve a concept step into either an API call or a focus marker."""
        if not step.facet or not step.action:
            raise ValueError(f"concept step {step.id} missing facet/action")
        semantics = self._deps.registry.semantic.get(step.facet)
        concept = semantics.find_concept(step.action) if semantics else None
        if concept is None:
            raise ValueError(f"unknown concept '{step.action}' for facet '{step.facet}'")

        if concept.api:
            return self._select_api(step, concept.api, resolved, focus=step.action)

        # Field-backed concept: mark as a focus relationship; no call needed.
        return {
            "step_id": step.id,
            "kind": "concept_field",
            "facet": step.facet,
            "api_name": None,
            "path_params": {},
            "query_params": {},
            "focus": step.action,
            "focus_field": concept.field,
            "target_facet": concept.target,
        }

    def _select_api(
        self, step: PlanStep, api_name: str, resolved: dict[str, Any], focus: str | None = None
    ) -> dict[str, Any]:
        """Build a call entry for an explicit endpoint, wiring its id param."""
        endpoint = self._deps.registry.get_endpoint(api_name)
        if endpoint is None:
            raise ValueError(f"unknown API '{api_name}'")
        path_params: dict[str, Any] = {}
        if endpoint.path_params:
            entity_id = self._resolve_id(step, resolved)
            if entity_id is None:
                raise ValueError(f"no id available for API '{api_name}'")
            path_params[endpoint.path_params[0]] = entity_id
        return self._call(step, endpoint.facet or step.facet, api_name, path_params=path_params, focus=focus)

    @staticmethod
    def _call(
        step: PlanStep,
        facet: str | None,
        api_name: str,
        *,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """Assemble a normalised selection entry."""
        return {
            "step_id": step.id,
            "kind": step.kind.value,
            "facet": facet,
            "api_name": api_name,
            "path_params": path_params or {},
            "query_params": query_params or {**step.params},
            "focus": focus,
        }

    @staticmethod
    def _resolve_id(step: PlanStep, resolved: dict[str, Any]) -> Any | None:
        """Find the entity id this step depends on.

        Prefers an explicit ``depends_on`` search step; falls back to the sole
        resolved entity when the plan is unambiguous.
        """
        for dep in step.depends_on:
            entity = resolved.get(str(dep))
            if entity and entity.get("id") is not None:
                return entity["id"]
        non_null = [e for e in resolved.values() if e.get("id") is not None]
        if len(non_null) == 1:
            return non_null[0]["id"]
        return None
