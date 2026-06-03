"""Tests for the JoinNode: linking prior step rows and feeding analytics."""

from __future__ import annotations

from types import SimpleNamespace

from src.models.plan import (
    AggregateOp,
    AggregateSpec,
    ExecutionPlan,
    JoinSpec,
    PlanStep,
    StepKind,
)
from src.nodes.analytics import AnalyticsNode
from src.nodes.join import JoinNode
from src.services.analytics_service import AnalyticsService
from src.services.joins import build_default_engine


def _deps() -> SimpleNamespace:
    facet = SimpleNamespace(display_fields=("name",))
    registry = SimpleNamespace(get_facet=lambda _f: facet)
    return SimpleNamespace(
        joins=build_default_engine(),
        analytics=AnalyticsService(),
        registry=registry,
    )


def _system_entry() -> dict:
    return {"step_id": 2, "kind": "get_by_id", "facet": "systems",
            "result": {"status": "success", "data": {"id": 5, "name": "CRM System", "ownerId": 7}}}


def _projects_entry() -> dict:
    rows = [
        {"id": 2, "name": "Payroll Automation", "ownerId": 7, "budget": 120000},
        {"id": 8, "name": "Customer Experience Initiative", "ownerId": 7, "budget": 250000},
        {"id": 9, "name": "Sales Automation", "ownerId": 7, "budget": 155000},
        {"id": 1, "name": "ERP Modernization", "ownerId": 1, "budget": 250000},
    ]
    return {"step_id": 3, "kind": "list", "facet": "projects",
            "result": {"status": "success", "data": rows}}


def _join_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal="crm owner projects",
        steps=[
            PlanStep(id=2, kind=StepKind.GET_BY_ID, facet="systems", depends_on=[1]),
            PlanStep(id=3, kind=StepKind.LIST, facet="projects"),
            PlanStep(
                id=4, kind=StepKind.JOIN, facet="projects", depends_on=[2, 3],
                join=JoinSpec(left_step=2, left_key="ownerId", right_step=3, right_key="ownerId"),
            ),
        ],
    )


async def test_join_node_injects_matched_rows() -> None:
    node = JoinNode(_deps())
    state = {
        "plan": _join_plan().model_dump(mode="json"),
        "resolved_results": [_system_entry(), _projects_entry()],
    }
    out = await node(state)
    join_entry = next(e for e in out["resolved_results"] if e["step_id"] == 4)
    names = [r["name"] for r in join_entry["result"]["data"]]
    assert names == ["Payroll Automation", "Customer Experience Initiative", "Sales Automation"]
    assert join_entry["facet"] == "projects"
    assert join_entry["result"]["status"] == "success"


async def test_aggregate_consumes_join_output() -> None:
    """avg budget of the CRM owner's projects = (120000+250000+155000)/3 = 175000."""
    deps = _deps()
    plan = _join_plan()
    plan.steps.append(
        PlanStep(id=5, kind=StepKind.AGGREGATE, facet="projects", depends_on=[4],
                 aggregate=AggregateSpec(op=AggregateOp.AVG, metric="budget"))
    )
    state = {
        "plan": plan.model_dump(mode="json"),
        "resolved_results": [_system_entry(), _projects_entry()],
    }
    join_out = await JoinNode(deps)(state)
    state["resolved_results"] = join_out["resolved_results"]

    analytics_out = await AnalyticsNode(deps)(state)
    assert analytics_out["analytics"][0]["value"] == 175000.0


async def test_join_node_reports_missing_rows() -> None:
    node = JoinNode(_deps())
    state = {
        "plan": _join_plan().model_dump(mode="json"),
        "resolved_results": [_system_entry()],  # projects list missing
    }
    out = await node(state)
    join_entry = next(e for e in out["resolved_results"] if e["step_id"] == 4)
    assert join_entry["result"]["status"] == "empty"
    assert any("join step 4" in e for e in out["errors"])


async def test_join_node_noop_without_join_steps() -> None:
    node = JoinNode(_deps())
    plan = ExecutionPlan(goal="x", steps=[PlanStep(id=1, kind=StepKind.LIST, facet="projects")])
    out = await node({"plan": plan.model_dump(mode="json"), "resolved_results": []})
    assert out == {}


# --- people belonging to an org unit (the child-of-parent path) -------------
def _org_unit_entry() -> dict:
    return {"step_id": 2, "kind": "get_by_id", "facet": "org_units",
            "result": {"status": "success", "data": {"id": 2, "name": "Finance Department"}}}


def _people_entry() -> dict:
    rows = [
        {"id": 1, "name": "Ahmed Mohamed", "orgUnitId": 3},
        {"id": 2, "name": "Sara Ali", "orgUnitId": 2},
        {"id": 4, "name": "Layla Ibrahim", "orgUnitId": 2},
        {"id": 7, "name": "Youssef Nabil", "orgUnitId": 2},
        {"id": 8, "name": "Ahmed Ali", "orgUnitId": 2},
        {"id": 9, "name": "Ahmed Hassan", "orgUnitId": 3},
    ]
    return {"step_id": 3, "kind": "list", "facet": "people",
            "result": {"status": "success", "data": rows}}


def _members_plan(with_count: bool = False) -> ExecutionPlan:
    steps = [
        PlanStep(id=2, kind=StepKind.GET_BY_ID, facet="org_units", depends_on=[1]),
        PlanStep(id=3, kind=StepKind.LIST, facet="people"),
        PlanStep(
            id=4, kind=StepKind.JOIN, facet="people", depends_on=[2, 3],
            join=JoinSpec(left_step=2, left_key="id", right_step=3, right_key="orgUnitId"),
        ),
    ]
    if with_count:
        steps.append(
            PlanStep(id=5, kind=StepKind.AGGREGATE, facet="people", depends_on=[4],
                     aggregate=AggregateSpec(op=AggregateOp.COUNT))
        )
    return ExecutionPlan(goal="people in finance", steps=steps)


async def test_join_lists_only_the_units_members() -> None:
    node = JoinNode(_deps())
    state = {
        "plan": _members_plan().model_dump(mode="json"),
        "resolved_results": [_org_unit_entry(), _people_entry()],
    }
    out = await node(state)
    join_entry = next(e for e in out["resolved_results"] if e["step_id"] == 4)
    names = [r["name"] for r in join_entry["result"]["data"]]
    assert names == ["Sara Ali", "Layla Ibrahim", "Youssef Nabil", "Ahmed Ali"]
    assert join_entry["facet"] == "people"


async def test_count_of_unit_members_is_four() -> None:
    """Regression for the '9 instead of 4' bug: counting must respect the unit."""
    deps = _deps()
    state = {
        "plan": _members_plan(with_count=True).model_dump(mode="json"),
        "resolved_results": [_org_unit_entry(), _people_entry()],
    }
    join_out = await JoinNode(deps)(state)
    state["resolved_results"] = join_out["resolved_results"]
    analytics_out = await AnalyticsNode(deps)(state)
    assert analytics_out["analytics"][0]["value"] == 4.0
