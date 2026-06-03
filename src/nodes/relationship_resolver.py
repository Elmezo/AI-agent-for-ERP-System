"""Relationship Resolver node.

The component that turns raw foreign keys into readable names. For each record
returned by the executor, it walks the facet's declared relationships and
replaces ids with human-friendly labels, e.g.::

    {"ownerId": 7}            -> {"ownerId": 7, "owner": "Ahmed Mohamed"}
    {"orgUnitId": 5}          -> {"orgUnitId": 5, "orgUnit": "Finance Department"}

Multi-hop is supported and made safe with:
  * a bounded depth (``MAX_REL_DEPTH``), and
  * a visited-set cycle guard keyed by ``(facet, id)``

so chains like ``dataset.createdBy -> person.orgUnitId -> orgUnit.managerId ->
person`` cannot loop forever.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from src.config.registry import Registry
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.observability.logging import get_logger
from src.services.facet_service import FacetService

_log = get_logger("node.relationship_resolver")


async def resolve_relationships(
    facets: FacetService,
    registry: Registry,
    facet: str,
    data: Any,
    max_depth: int,
) -> Any:
    """Resolve foreign keys in ``data`` (a dict or list of dicts) into names.

    Returns a deep-copied, resolved structure; the input is not mutated. Each
    top-level record is resolved with its own visited set so every item is fully
    expanded while remaining cycle-safe within its own traversal.
    """
    if isinstance(data, list):
        return [
            await _resolve_record(facets, registry, facet, copy.deepcopy(rec), 0, max_depth, set())
            for rec in data
            if isinstance(rec, dict)
        ]
    if isinstance(data, dict):
        return await _resolve_record(
            facets, registry, facet, copy.deepcopy(data), 0, max_depth, set()
        )
    return data


async def _resolve_record(
    facets: FacetService,
    registry: Registry,
    facet: str,
    record: dict[str, Any],
    depth: int,
    max_depth: int,
    visited: set[tuple[str, Any]],
) -> dict[str, Any]:
    """Resolve relationships on a single record, recursing depth-first."""
    facet_def = registry.get_facet(facet)
    if facet_def is None:
        return record

    pk_value = record.get(facet_def.primary_key)
    key = (facet, pk_value)
    if key in visited or depth >= max_depth:
        return record
    visited.add(key)

    for field, relationship in facet_def.relationships.items():
        fk_value = record.get(field)
        if fk_value is None:
            continue
        target = await facets.resolve_record(relationship.target_facet, fk_value)
        if target is None:
            continue
        record[relationship.resolved_name()] = facets.display_name(
            relationship.target_facet, target
        )
        # Recurse so deeper relationships are traversed (cycle-guarded). Only the
        # readable label is surfaced at each level to keep the payload compact.
        if depth + 1 < max_depth:
            await _resolve_record(
                facets, registry, relationship.target_facet, target,
                depth + 1, max_depth, visited,
            )
    return record


class RelationshipResolverNode:
    """Resolve foreign keys for every executed result into readable names."""

    def __init__(self, deps) -> None:  # type: ignore[no-untyped-def]
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Produce ``resolved_results`` mirroring ``execution_results``."""
        execution_results = state.get("execution_results", [])
        start = time.perf_counter()
        resolved_results: list[dict[str, Any]] = []
        resolved_count = 0

        for entry in execution_results:
            result = entry.get("result")
            facet = entry.get("facet")
            if not result or result.get("status") not in {"success"} or facet is None:
                resolved_results.append(entry)
                continue
            resolved_data = await resolve_relationships(
                self._deps.facets,
                self._deps.registry,
                facet,
                result.get("data"),
                self._deps.max_rel_depth,
            )
            new_result = {**result, "data": resolved_data}
            resolved_results.append({**entry, "result": new_result})
            resolved_count += 1

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("relationships_resolved", records=resolved_count)
        return {
            "resolved_results": resolved_results,
            "trace": append_trace(
                state, "relationship_resolver", elapsed,
                f"resolved={resolved_count} max_depth={self._deps.max_rel_depth}",
            ),
        }
