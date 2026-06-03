"""Tests for the pluggable join framework."""

from __future__ import annotations

from src.models.plan import JoinSpec, JoinType
from src.services.joins import JoinEngine, NestedLoopJoin, build_default_engine
from src.services.joins.base import keys_match

_SYSTEMS = [{"id": 5, "name": "CRM System", "ownerId": 7}]
_PROJECTS = [
    {"id": 2, "name": "Payroll Automation", "ownerId": 7},
    {"id": 8, "name": "Customer Experience Initiative", "ownerId": 7},
    {"id": 1, "name": "ERP Modernization", "ownerId": 1},
]


def test_keys_match_coerces_numbers() -> None:
    assert keys_match(7, "7") is True
    assert keys_match("Finance", "finance") is True
    assert keys_match(None, None) is False
    assert keys_match(7, 8) is False


def test_nested_loop_inner_join_emits_matches() -> None:
    spec = JoinSpec(left_step=2, left_key="ownerId", right_step=3, right_key="ownerId")
    pairs = NestedLoopJoin().join(_SYSTEMS, _PROJECTS, "ownerId", "ownerId", JoinType.INNER)
    matched_names = [p.right["name"] for p in pairs]
    assert matched_names == ["Payroll Automation", "Customer Experience Initiative"]
    assert all(p.left["name"] == "CRM System" for p in pairs)


def test_left_join_keeps_unmatched_left() -> None:
    left = [{"id": 99, "ownerId": 42}]
    pairs = NestedLoopJoin().join(left, _PROJECTS, "ownerId", "ownerId", JoinType.LEFT)
    assert len(pairs) == 1
    assert pairs[0].right is None


def test_engine_uses_default_strategy() -> None:
    engine = build_default_engine()
    spec = JoinSpec(left_step=2, left_key="ownerId", right_step=3, right_key="ownerId")
    pairs = engine.join(_SYSTEMS, _PROJECTS, spec)
    assert len(pairs) == 2


def test_engine_falls_back_when_strategy_unknown() -> None:
    engine = build_default_engine()
    spec = JoinSpec(left_step=2, left_key="ownerId", right_step=3, right_key="ownerId", strategy="hash")
    # "hash" isn't registered yet -> engine degrades to the default, never crashes.
    assert len(engine.join(_SYSTEMS, _PROJECTS, spec)) == 2


def test_engine_register_new_strategy() -> None:
    class AllPairs(NestedLoopJoin):
        name = "all_pairs"

    engine = JoinEngine(default=NestedLoopJoin.name)
    engine.register(NestedLoopJoin())
    engine.register(AllPairs())
    spec = JoinSpec(left_step=1, left_key="ownerId", right_step=2, right_key="ownerId", strategy="all_pairs")
    assert len(engine.join(_SYSTEMS, _PROJECTS, spec)) == 2
