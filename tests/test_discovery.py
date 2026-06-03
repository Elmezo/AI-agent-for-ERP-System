"""Tests for the API discovery layer."""

from __future__ import annotations

import json
from pathlib import Path

from src.discovery.generator import endpoints_to_registry, generate
from src.discovery.openapi_source import OpenApiSource
from src.discovery.postman_source import PostmanSource

OPENAPI = Path("config/sources/openapi.json")
POSTMAN = Path("config/sources/postman_collection.json")


def test_openapi_source_parses_endpoints() -> None:
    endpoints = {e.name: e for e in OpenApiSource(OPENAPI).load()}
    assert "people.get_by_id" in endpoints
    ep = endpoints["people.get_by_id"]
    assert ep.path_params == ("id",)
    assert ep.facet == "people"
    search = endpoints["people.search"]
    assert "q" in search.query_params


def test_postman_source_parses_path_vars() -> None:
    endpoints = {e.name: e for e in PostmanSource(POSTMAN).load()}
    assert "people.get_by_id" in endpoints
    assert endpoints["people.get_by_id"].url == "/api/people/{id}"
    assert endpoints["people.get_by_id"].path_params == ("id",)


def test_generate_writes_registry(tmp_path: Path) -> None:
    out = tmp_path / "registry.json"
    registry = generate("openapi", OPENAPI, out)
    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == registry
    assert registry["systems.stakeholders"]["path_params"] == ["id"]


def test_endpoints_to_registry_roundtrip() -> None:
    endpoints = OpenApiSource(OPENAPI).load()
    registry = endpoints_to_registry(endpoints)
    assert registry["people.list"]["method"] == "GET"
    assert "path_params" not in registry["people.list"]  # list has none
