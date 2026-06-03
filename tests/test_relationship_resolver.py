"""Tests for cycle-safe relationship resolution."""

from __future__ import annotations

from typing import Any

from src.config.registry import Registry
from src.nodes.relationship_resolver import resolve_relationships


class FakeFacets:
    """Stub facet service returning canned records without any HTTP."""

    def __init__(self, data: dict[str, dict[Any, dict]]) -> None:
        self.data = data
        self.lookups = 0

    async def resolve_record(self, facet: str, entity_id: Any) -> dict | None:
        self.lookups += 1
        return self.data.get(facet, {}).get(entity_id)

    def display_name(self, facet: str, record: dict) -> str:
        return record.get("name", str(record.get("id")))


async def test_fk_resolved_to_name(registry: Registry) -> None:
    facets = FakeFacets({"people": {1: {"id": 1, "name": "Ahmed Mohamed"}}})
    record = {"id": 3, "name": "System ABC", "ownerId": 1}
    resolved = await resolve_relationships(facets, registry, "systems", record, max_depth=3)
    assert resolved["owner"] == "Ahmed Mohamed"
    assert resolved["ownerId"] == 1  # original retained


async def test_list_resolution(registry: Registry) -> None:
    facets = FakeFacets(
        {
            "people": {7: {"id": 7, "name": "Youssef"}},
            "org_units": {2: {"id": 2, "name": "Finance Department"}},
        }
    )
    data = [{"id": 1, "name": "Payroll", "createdBy": 7, "orgUnitId": 2}]
    resolved = await resolve_relationships(facets, registry, "datasets", data, max_depth=3)
    assert resolved[0]["createdByName"] == "Youssef"
    assert resolved[0]["orgUnit"] == "Finance Department"


async def test_cycle_does_not_loop(registry: Registry) -> None:
    # org_units.managerId -> people ; people.orgUnitId -> org_units  (a cycle)
    facets = FakeFacets(
        {
            "people": {5: {"id": 5, "name": "Khaled", "orgUnitId": 1}},
            "org_units": {1: {"id": 1, "name": "Executive", "managerId": 5}},
        }
    )
    record = {"id": 5, "name": "Khaled", "orgUnitId": 1}
    resolved = await resolve_relationships(facets, registry, "people", record, max_depth=3)
    # Completes (no infinite loop) and resolves the direct relationship.
    assert resolved["orgUnit"] == "Executive"


async def test_depth_zero_does_nothing(registry: Registry) -> None:
    facets = FakeFacets({"people": {1: {"id": 1, "name": "Ahmed"}}})
    record = {"id": 3, "name": "System ABC", "ownerId": 1}
    resolved = await resolve_relationships(facets, registry, "systems", record, max_depth=0)
    assert "owner" not in resolved
