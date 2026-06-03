"""Tests for ambiguity detection in the entity resolver node."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.config.registry import Registry
from src.models.api import ApiResult
from src.models.plan import ExecutionPlan, PlanStep, StepKind
from src.nodes.entity_resolver import EntityResolverNode


class _FakeFacets:
    """Stub facet service whose search returns canned records."""

    def __init__(
        self, records: list[dict[str, Any]], by_id: dict[int, dict[str, Any]] | None = None
    ) -> None:
        self._records = records
        self._by_id = by_id or {}
        self.search_calls = 0

    async def search(self, facet: str, term: str) -> ApiResult:
        self.search_calls += 1
        return ApiResult.success(f"{facet}.search", self._records)

    async def resolve_record(self, facet: str, entity_id: Any) -> dict[str, Any] | None:
        return self._by_id.get(int(entity_id))

    def display_name(self, facet: str, record: dict[str, Any]) -> str:
        return str(record.get("name", record.get("id")))


@dataclass
class _FakeDeps:
    facets: Any
    registry: Registry


def _state(query: str, question: str) -> dict[str, Any]:
    plan = ExecutionPlan(
        goal="lookup",
        steps=[PlanStep(id=1, kind=StepKind.SEARCH, facet="people", query=query)],
    )
    return {"plan": plan.model_dump(mode="json"), "user_input": question}


async def test_ambiguous_search_requests_clarification(registry: Registry) -> None:
    facets = _FakeFacets(
        [
            {"id": 1, "name": "Ahmed Mohamed", "title": "CTO"},
            {"id": 8, "name": "Ahmed Ali", "title": "Sales Manager"},
            {"id": 9, "name": "Ahmed Hassan", "title": "IT Support"},
        ]
    )
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state("Ahmed", "Show Ahmed's projects"))

    clar = out["clarification"]
    assert clar["needed"] is True
    assert clar["query"] == "Ahmed"
    assert clar["original_question"] == "Show Ahmed's projects"
    assert len(clar["candidates"]) == 3
    # The ambiguous step is NOT resolved to a guessed id.
    assert out["resolved_entities"]["1"]["id"] is None
    assert out["resolved_entities"]["1"]["ambiguous"] is True


async def test_single_match_resolves_without_clarification(registry: Registry) -> None:
    facets = _FakeFacets([{"id": 1, "name": "Ahmed Mohamed", "title": "CTO"}])
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state("Ahmed Mohamed", "Show Ahmed Mohamed's projects"))

    assert "clarification" not in out
    assert out["resolved_entities"]["1"]["id"] == 1
    assert out["resolved_entities"]["1"]["label"] == "Ahmed Mohamed"


def _state_for(facet: str, query: str, question: str) -> dict[str, Any]:
    plan = ExecutionPlan(
        goal="lookup",
        steps=[PlanStep(id=1, kind=StepKind.SEARCH, facet=facet, query=query)],
    )
    return {"plan": plan.model_dump(mode="json"), "user_input": question}


async def test_numeric_query_resolves_by_primary_key(registry: Registry) -> None:
    """'org unit 2' -> resolve org_units id=2 directly, bypassing fuzzy search."""
    facets = _FakeFacets(
        records=[{"id": 3, "name": "IT Department"}],  # what a text search would wrongly return
        by_id={2: {"id": 2, "name": "Finance Department"}},
    )
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state_for("org_units", "2", "people in org unit 2"))

    assert out["resolved_entities"]["1"]["id"] == 2
    assert out["resolved_entities"]["1"]["label"] == "Finance Department"
    assert facets.search_calls == 0  # resolved by id, never fell back to search


async def test_hash_id_query_resolves_by_primary_key(registry: Registry) -> None:
    facets = _FakeFacets(records=[], by_id={2: {"id": 2, "name": "Finance Department"}})
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state_for("org_units", "#2", "names in #2"))

    assert out["resolved_entities"]["1"]["id"] == 2


async def test_missing_numeric_id_falls_back_to_search(registry: Registry) -> None:
    """An id with no record falls through to a normal search rather than failing."""
    facets = _FakeFacets(records=[{"id": 99, "name": "Found via search"}], by_id={})
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state_for("org_units", "42", "org unit 42"))

    assert facets.search_calls == 1
    assert out["resolved_entities"]["1"]["id"] == 99


async def test_exact_match_among_partials_resolves(registry: Registry) -> None:
    facets = _FakeFacets(
        [
            {"id": 1, "name": "Ahmed Mohamed", "title": "CTO"},
            {"id": 8, "name": "Ahmed Ali", "title": "Sales Manager"},
        ]
    )
    node = EntityResolverNode(_FakeDeps(facets, registry))

    out = await node(_state("Ahmed Mohamed", "Ahmed Mohamed's projects"))

    assert "clarification" not in out
    assert out["resolved_entities"]["1"]["id"] == 1
