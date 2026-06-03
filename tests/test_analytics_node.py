"""Tests for the AnalyticsNode: wiring list rows to the analytics service."""

from __future__ import annotations

from types import SimpleNamespace

from src.models.plan import AggregateOp, AggregateSpec, ExecutionPlan, PlanStep, StepKind
from src.nodes.analytics import AnalyticsNode
from src.services.analytics_service import AnalyticsService


def _deps(registry=None) -> SimpleNamespace:
    facet = SimpleNamespace(display_fields=("name",))
    registry = registry or SimpleNamespace(get_facet=lambda _f: facet)
    return SimpleNamespace(analytics=AnalyticsService(), registry=registry)


def _list_entry(step_id: int, facet: str, rows: list[dict]) -> dict:
    return {
        "step_id": step_id,
        "facet": facet,
        "result": {"status": "success", "data": rows},
    }


_ROWS = [
    {"id": 1, "name": "A", "budget": 250000, "status": "Active"},
    {"id": 2, "name": "B", "budget": 120000, "status": "Completed"},
    {"id": 3, "name": "C", "budget": 300000, "status": "Active"},
]


async def test_node_computes_aggregate_from_depends_on_list() -> None:
    node = AnalyticsNode(_deps())
    plan = ExecutionPlan(
        goal="avg budget",
        steps=[
            PlanStep(id=1, kind=StepKind.LIST, facet="projects"),
            PlanStep(
                id=2, kind=StepKind.AGGREGATE, facet="projects", depends_on=[1],
                aggregate=AggregateSpec(op=AggregateOp.AVG, metric="budget"),
            ),
        ],
    )
    state = {"plan": plan.model_dump(mode="json"), "resolved_results": [_list_entry(1, "projects", _ROWS)]}

    out = await node(state)
    assert "analytics" in out
    assert out["analytics"][0]["value"] == 223333.3333


async def test_node_falls_back_to_facet_match_without_depends_on() -> None:
    node = AnalyticsNode(_deps())
    plan = ExecutionPlan(
        goal="top 1",
        steps=[
            PlanStep(id=1, kind=StepKind.LIST, facet="projects"),
            PlanStep(
                id=2, kind=StepKind.AGGREGATE, facet="projects", depends_on=[],
                aggregate=AggregateSpec(op=AggregateOp.MAX, metric="budget", limit=1),
            ),
        ],
    )
    state = {"plan": plan.model_dump(mode="json"), "resolved_results": [_list_entry(99, "projects", _ROWS)]}

    out = await node(state)
    groups = out["analytics"][0]["groups"]
    assert groups[0]["key"] == "C" and groups[0]["value"] == 300000.0


async def test_node_noop_without_aggregate_steps() -> None:
    node = AnalyticsNode(_deps())
    plan = ExecutionPlan(goal="list", steps=[PlanStep(id=1, kind=StepKind.LIST, facet="projects")])
    out = await node({"plan": plan.model_dump(mode="json"), "resolved_results": []})
    assert out == {}
