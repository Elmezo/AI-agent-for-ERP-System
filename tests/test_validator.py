"""Tests for the response validator node's classification logic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.nodes.response_validator import ResponseValidatorNode


@pytest.fixture
def node() -> ResponseValidatorNode:
    return ResponseValidatorNode(SimpleNamespace())


async def test_ok_with_item(node: ResponseValidatorNode) -> None:
    state = {"language": "en", "context": {"results": [{"status": "success", "item": {"id": 1}}], "focus": []}}
    out = await node(state)
    assert out["validation"]["status"] == "ok"


async def test_ok_with_count(node: ResponseValidatorNode) -> None:
    state = {"language": "en", "context": {"results": [{"status": "success", "count": 7}], "focus": []}}
    out = await node(state)
    assert out["validation"]["status"] == "ok"


async def test_empty(node: ResponseValidatorNode) -> None:
    state = {"language": "ar", "context": {"results": [{"status": "empty", "data": None}], "focus": []}}
    out = await node(state)
    assert out["validation"]["status"] == "empty"
    assert "لا توجد" in out["validation"]["message"]


async def test_error(node: ResponseValidatorNode) -> None:
    state = {"language": "en", "context": {"results": [{"status": "error", "error": "boom"}], "focus": []}}
    out = await node(state)
    assert out["validation"]["status"] == "error"


async def test_no_plan(node: ResponseValidatorNode) -> None:
    state = {"language": "en", "context": {"results": [], "focus": []}}
    out = await node(state)
    assert out["validation"]["status"] == "no_plan"


async def test_focus_value_makes_ok(node: ResponseValidatorNode) -> None:
    state = {
        "language": "en",
        "context": {"results": [], "focus": [{"concept": "owner", "value": "Ahmed"}]},
    }
    out = await node(state)
    assert out["validation"]["status"] == "ok"


async def test_zero_count_not_misleading(node: ResponseValidatorNode) -> None:
    state = {"language": "en", "context": {"results": [{"status": "success", "count": 0}], "focus": []}}
    out = await node(state)
    # A successful-but-empty list must not be reported as usable data.
    assert out["validation"]["status"] == "empty"
