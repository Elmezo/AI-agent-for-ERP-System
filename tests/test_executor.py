"""Tests for the executor node."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.models.api import ApiResult
from src.nodes.executor import ExecutorNode


async def test_executes_calls_and_passes_through_markers() -> None:
    client = SimpleNamespace(
        call=AsyncMock(return_value=ApiResult.success("systems.get_by_id", {"id": 3, "name": "ABC"}))
    )
    deps = SimpleNamespace(client=client)
    node = ExecutorNode(deps)

    state = {
        "selected_apis": [
            {"step_id": 2, "kind": "get_by_id", "facet": "systems",
             "api_name": "systems.get_by_id", "path_params": {"id": 3}, "query_params": {}},
            {"step_id": 3, "kind": "concept_field", "facet": "systems",
             "api_name": None, "focus": "owner", "focus_field": "ownerId"},
        ]
    }
    out = await node(state)
    results = out["execution_results"]
    assert len(results) == 2
    executed = next(r for r in results if r["api_name"] == "systems.get_by_id")
    assert executed["result"]["status"] == "success"
    marker = next(r for r in results if r.get("kind") == "concept_field")
    assert marker["result"] is None
    client.call.assert_awaited_once()


async def test_collects_errors() -> None:
    client = SimpleNamespace(
        call=AsyncMock(return_value=ApiResult.failure("people.list", "HTTP 500"))
    )
    node = ExecutorNode(SimpleNamespace(client=client))
    state = {
        "selected_apis": [
            {"step_id": 1, "kind": "list", "facet": "people",
             "api_name": "people.list", "path_params": {}, "query_params": {}},
        ]
    }
    out = await node(state)
    assert out["errors"]
    assert "people.list" in out["errors"][0]
