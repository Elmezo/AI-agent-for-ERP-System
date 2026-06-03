"""Tests for the rule-based fallback planner."""

from __future__ import annotations

from src.config.registry import Registry
from src.models.plan import PlanIntent, StepKind
from src.planner.fallback_planner import FallbackPlanner, detect_language


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
