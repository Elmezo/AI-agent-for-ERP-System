"""Tests for analytics formatting in the Context Builder."""

from __future__ import annotations

from types import SimpleNamespace

from src.models.analytics import AnalyticsGroup, AnalyticsResult
from src.nodes.context_builder import ContextBuilderNode
from src.models.plan import ExecutionPlan, PlanStep, StepKind


def _node() -> ContextBuilderNode:
    facet = SimpleNamespace(business_name="Projects", relationships={})
    registry = SimpleNamespace(get_facet=lambda _f: facet)
    return ContextBuilderNode(SimpleNamespace(registry=registry, max_rel_depth=2))


def _state(analytics: list[AnalyticsResult], question: str = "q") -> dict:
    plan = ExecutionPlan(
        goal=question,
        steps=[PlanStep(id=1, kind=StepKind.LIST, facet="projects")],
    )
    return {
        "plan": plan.model_dump(mode="json"),
        "user_input": question,
        "language": "en",
        "analytics": [a.model_dump(mode="json") for a in analytics],
    }


async def test_scalar_avg_is_formatted() -> None:
    node = _node()
    result = AnalyticsResult(facet="projects", op="avg", metric="budget", value=180000.0, total_rows=5, matched_rows=5)
    out = await node(_state([result], "average budget"))
    focus = out["context"]["focus"][0]
    assert focus["concept"] == "analytics"
    assert focus["value"] == "Average budget: 180,000"


async def test_groups_are_formatted_as_list() -> None:
    node = _node()
    result = AnalyticsResult(
        facet="projects", op="count", group_by="orgUnit",
        groups=[AnalyticsGroup(key="IT Department", value=2, count=2),
                AnalyticsGroup(key="Finance Department", value=3, count=3)],
    )
    out = await node(_state([result], "projects by department"))
    value = out["context"]["focus"][0]["value"]
    assert "Projects by orgUnit:" in value
    assert "- IT Department: 2" in value
    assert "- Finance Department: 3" in value


async def test_top_n_header() -> None:
    node = _node()
    result = AnalyticsResult(
        facet="projects", op="max", metric="budget",
        groups=[AnalyticsGroup(key="Data Lake", value=300000, count=1)],
    )
    out = await node(_state([result], "top projects"))
    value = out["context"]["focus"][0]["value"]
    assert "Top 1 Projects by budget:" in value
    assert "- Data Lake: 300,000" in value


async def test_error_is_surfaced_truthfully() -> None:
    node = _node()
    result = AnalyticsResult(facet="projects", op="avg", metric="budget", error="field 'budget' not found")
    out = await node(_state([result], "average budget"))
    value = out["context"]["focus"][0]["value"]
    assert value == "I couldn't compute that: field 'budget' not found."
