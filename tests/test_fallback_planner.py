"""Tests for the rule-based fallback planner."""

from __future__ import annotations

from src.config.registry import Registry
from src.models.plan import AggregateOp, FilterOp, PlanIntent, StepKind
from src.planner.fallback_planner import FallbackPlanner, detect_language


def _aggregate_step(plan):
    return next((s for s in plan.steps if s.kind is StepKind.AGGREGATE), None)


def test_detect_language() -> None:
    assert detect_language("how many?") == "en"
    assert detect_language("كم عدد الموظفين") == "ar"


def test_owner_question_produces_search_get_concept(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("Who owns System ABC?")
    kinds = [s.kind for s in plan.steps]
    assert kinds == [StepKind.SEARCH, StepKind.GET_BY_ID, StepKind.CONCEPT]
    assert plan.steps[0].facet == "systems"
    assert plan.steps[0].query == "System ABC"
    assert plan.steps[2].action == "owner"
    assert plan.used_fallback is True


def test_count_question_produces_list(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("How many employees are there?")
    assert [s.kind for s in plan.steps] == [StepKind.LIST]
    assert plan.steps[0].facet == "people"


def test_arabic_count(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("كم عدد الموظفين؟")
    assert plan.language == "ar"
    assert plan.steps[0].kind == StepKind.LIST
    assert plan.steps[0].facet == "people"


def test_created_concept(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("Who created dataset Payroll 2025?")
    kinds = [s.kind for s in plan.steps]
    assert StepKind.CONCEPT in kinds
    concept = next(s for s in plan.steps if s.kind == StepKind.CONCEPT)
    assert concept.action == "creator"


def test_unknown_facet_yields_empty_plan(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("What is the weather today?")
    assert plan.is_empty


def test_recall_question_yields_recall_intent(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("what is the last question i asked you about?")
    assert plan.intent is PlanIntent.RECALL
    assert plan.recall_topic == "previous_question"
    assert plan.steps == []
    # RECALL plans have no steps but are NOT "empty" (there is something to answer).
    assert plan.is_empty is False
    assert plan.used_fallback is True


def test_manages_detects_manager_concept(registry: Registry) -> None:
    """Regression: "manages" (not just "manager") must trigger the concept step."""
    plan = FallbackPlanner(registry).plan("Who manages the Finance Department?")
    concept = next((s for s in plan.steps if s.kind == StepKind.CONCEPT), None)
    assert concept is not None
    assert concept.action == "manager"


# --- analytics fallback (LLM-free) -----------------------------------------
def test_average_budget_builds_avg_aggregate(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("What is the average project budget?")
    assert [s.kind for s in plan.steps] == [StepKind.LIST, StepKind.AGGREGATE]
    spec = _aggregate_step(plan).aggregate
    assert spec.op is AggregateOp.AVG and spec.metric == "budget"


def test_top_n_projects_by_budget(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("Top 5 projects by budget")
    spec = _aggregate_step(plan).aggregate
    assert spec.metric == "budget" and spec.limit == 5 and spec.sort_desc is True


def test_count_active_projects_filter(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("How many projects are active?")
    spec = _aggregate_step(plan).aggregate
    assert spec.op is AggregateOp.COUNT
    assert any(f.field == "status" and f.value == "Active" for f in spec.filters)


def test_datasets_belong_to_finance_filter(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("How many datasets belong to Finance?")
    spec = _aggregate_step(plan).aggregate
    assert spec.op is AggregateOp.COUNT
    assert any(
        f.field == "orgUnit" and f.op is FilterOp.CONTAINS and "Finance" in str(f.value)
        for f in spec.filters
    )


def test_projects_grouped_by_owner(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("Show projects grouped by owner")
    spec = _aggregate_step(plan).aggregate
    assert spec.group_by == "owner"


def test_plain_count_stays_list_not_aggregate(registry: Registry) -> None:
    """A bare 'how many projects' is just a list count, not an aggregate step."""
    plan = FallbackPlanner(registry).plan("How many projects are there?")
    assert _aggregate_step(plan) is None
    assert plan.steps[0].kind is StepKind.LIST


# --- child-of-parent (people in an org unit) fallback ----------------------
def test_people_in_org_unit_by_id_builds_join(registry: Registry) -> None:
    """'names of the people in org unit 2' -> search+get_by_id+list+join, no count."""
    plan = FallbackPlanner(registry).plan("What are the names of the people in org unit 2?")
    kinds = [s.kind for s in plan.steps]
    assert kinds == [StepKind.SEARCH, StepKind.GET_BY_ID, StepKind.LIST, StepKind.JOIN]
    assert plan.steps[0].facet == "org_units"
    assert plan.steps[0].query == "2"  # resolved by id, not fuzzy text
    assert plan.steps[2].facet == "people"
    join = plan.steps[-1].join
    assert join is not None
    assert join.left_step == 2 and join.left_key == "id"
    assert join.right_step == 3 and join.right_key == "orgUnitId"
    assert join.emit == "right"


def test_count_people_in_org_unit_adds_count_aggregate(registry: Registry) -> None:
    """'how many people in orgunit number 2' -> the join plus a count aggregate."""
    plan = FallbackPlanner(registry).plan("How number of people in orgunit number 2?")
    kinds = [s.kind for s in plan.steps]
    assert kinds == [
        StepKind.SEARCH, StepKind.GET_BY_ID, StepKind.LIST, StepKind.JOIN, StepKind.AGGREGATE,
    ]
    assert plan.steps[0].query == "2"
    agg = plan.steps[-1]
    assert agg.depends_on == [4]
    assert agg.aggregate is not None and agg.aggregate.op is AggregateOp.COUNT


def test_employees_of_named_department_builds_join(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan("List the members of the Finance Department")
    kinds = [s.kind for s in plan.steps]
    assert kinds[:4] == [StepKind.SEARCH, StepKind.GET_BY_ID, StepKind.LIST, StepKind.JOIN]
    assert plan.steps[0].facet == "org_units"
    assert "Finance" in (plan.steps[0].query or "")


def test_who_manages_is_not_misread_as_members(registry: Registry) -> None:
    """Regression: a manager-concept question must NOT trigger the members join."""
    plan = FallbackPlanner(registry).plan("Who manages the Finance Department?")
    assert StepKind.JOIN not in [s.kind for s in plan.steps]
    concept = next((s for s in plan.steps if s.kind == StepKind.CONCEPT), None)
    assert concept is not None and concept.action == "manager"


# --- cross-entity join fallback --------------------------------------------
def test_join_chain_for_crm_owner_projects(registry: Registry) -> None:
    plan = FallbackPlanner(registry).plan(
        "Who owns the CRM system and what projects is he working on?"
    )
    kinds = [s.kind for s in plan.steps]
    assert kinds == [StepKind.SEARCH, StepKind.GET_BY_ID, StepKind.LIST, StepKind.JOIN]
    assert plan.steps[0].query == "CRM"
    join = plan.steps[-1].join
    assert join is not None
    assert join.left_step == 2 and join.left_key == "ownerId"
    assert join.right_step == 3 and join.right_key == "ownerId"
