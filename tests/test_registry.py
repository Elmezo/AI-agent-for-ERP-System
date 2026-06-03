"""Tests for configuration loading into the typed Registry."""

from __future__ import annotations

from src.config.registry import Registry


def test_endpoints_loaded(registry: Registry) -> None:
    assert registry.get_endpoint("people.get_by_id") is not None
    assert registry.get_endpoint("nope") is None


def test_facets_and_relationships(registry: Registry) -> None:
    systems = registry.require_facet("systems")
    assert systems.primary_key == "id"
    assert "ownerId" in systems.relationships
    rel = systems.relationships["ownerId"]
    assert rel.target_facet == "people"
    assert rel.resolved_name() == "owner"


def test_dataset_relationship_names(registry: Registry) -> None:
    datasets = registry.require_facet("datasets")
    assert datasets.relationships["createdBy"].resolved_name() == "createdByName"
    assert datasets.relationships["orgUnitId"].resolved_name() == "orgUnit"


def test_semantic_catalog(registry: Registry) -> None:
    systems = registry.semantic.get("systems")
    assert systems is not None
    owner = systems.find_concept("owner")
    assert owner is not None and owner.field == "ownerId" and owner.target == "people"
    # synonym
    assert systems.find_concept("responsible") is not None


def test_catalog_summary_mentions_facets(registry: Registry) -> None:
    summary = registry.catalog_summary()
    for facet in ("people", "systems", "datasets", "org_units"):
        assert facet in summary
