"""Entity Resolver node.

Turns named entities into concrete ids by executing the plan's ``search`` steps.
For "who owns System ABC?", this resolves "System ABC" -> system id, which later
steps (get_by_id / concept) depend on.
"""

from __future__ import annotations

import re
import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, StepKind
from src.models.state import AgentState
from src.nodes._helpers import append_errors, append_trace
from src.nodes.clarification import build_clarification, disambiguate
from src.observability.logging import get_logger

_log = get_logger("node.entity_resolver")

# A query that is just an id ("2", "#2", "id 7") should be resolved by primary
# key directly rather than by fuzzy text search, which is unreliable on numbers.
_ID_QUERY = re.compile(r"^\s*(?:#|id\s+|no\.?\s*|رقم\s*)?(\d+)\s*$", re.IGNORECASE)


def parse_id_query(query: str | None) -> int | None:
    """Return the integer id a query denotes (``"#2"`` -> ``2``), else ``None``."""
    if not query:
        return None
    match = _ID_QUERY.match(query)
    return int(match.group(1)) if match else None


class EntityResolverNode:
    """Resolve ``search`` steps into ``{step_id: {facet, id, label, record}}``.

    When a search is genuinely ambiguous (several plausible matches, no single
    exact one), it stops and requests clarification instead of guessing the first
    result. Downstream routing skips execution for that turn and asks the user.
    """

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Execute search steps; resolve a single match or ask to disambiguate."""
        plan = ExecutionPlan(**state["plan"])
        start = time.perf_counter()
        resolved: dict[str, Any] = {}
        errors: list[str] = []
        clarification: dict[str, Any] | None = None

        for step in plan.steps:
            if step.kind is not StepKind.SEARCH or not step.facet:
                continue

            # Deterministic shortcut: a bare id ("#2", "2") is resolved by
            # primary key, so "org unit 2" never depends on text-search luck.
            entity_id = parse_id_query(step.query)
            if entity_id is not None:
                direct = await self._resolve_by_id(step.facet, entity_id)
                if direct is not None:
                    resolved[str(step.id)] = direct
                    continue
                # No such id: fall through to a normal search as a last resort.

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

            record, candidates = disambiguate(
                step.facet, step.query or "", result.data, self._deps.facets
            )
            if candidates is not None:
                # Ambiguous: don't guess. Ask the user which one they meant.
                clarification = build_clarification(
                    step.facet, step.query or "", state["user_input"], candidates
                )
                resolved[str(step.id)] = {
                    "facet": step.facet, "id": None, "label": None,
                    "record": None, "ambiguous": True,
                }
                _log.info("entity_ambiguous", facet=step.facet, query=step.query, options=len(candidates))
                break  # one clarification at a time

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
        return self._finalize(state, resolved, clarification, errors, elapsed)

    async def _resolve_by_id(self, facet: str, entity_id: int) -> dict[str, Any] | None:
        """Resolve a facet directly by primary key, or ``None`` if not found."""
        record = await self._deps.facets.resolve_record(facet, entity_id)
        if not isinstance(record, dict):
            return None
        facet_def = self._deps.registry.get_facet(facet)
        pk = facet_def.primary_key if facet_def else "id"
        _log.info("entity_resolved_by_id", facet=facet, id=entity_id)
        return {
            "facet": facet,
            "id": record.get(pk, entity_id),
            "label": self._deps.facets.display_name(facet, record),
            "record": record,
        }

    @staticmethod
    def _finalize(
        state: AgentState,
        resolved: dict[str, Any],
        clarification: dict[str, Any] | None,
        errors: list[str],
        elapsed: float,
    ) -> dict[str, Any]:
        """Assemble the node's state update from the resolution outcome."""
        update: dict[str, Any] = {
            "resolved_entities": resolved,
            "trace": append_trace(state, "entity_resolver", elapsed, f"resolved={len(resolved)}"),
        }
        if clarification is not None:
            update["clarification"] = clarification
        if errors:
            update["errors"] = append_errors(state, errors)
        return update
