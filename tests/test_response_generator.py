"""Tests for the Response Generator's deterministic (LLM-down) fallback.

These guard the regression where the agent dumped raw JSON instead of a
human-readable answer when the response LLM was unavailable.
"""

from __future__ import annotations

from src.nodes.response_generator import ResponseGeneratorNode

_fallback = ResponseGeneratorNode._fallback_answer


def _ctx(**over):
    base = {"question": "", "focus": [], "results": []}
    base.update(over)
    return base


def test_focus_value_returns_clean_name() -> None:
    ctx = _ctx(
        question="Who manages the Finance Department?",
        focus=[{"concept": "manager", "field": "managerId", "value": "Youssef Nabil"}],
    )
    assert _fallback(ctx, "en") == "Youssef Nabil"


def test_question_field_match_returns_manager_without_focus() -> None:
    """No focus marker (e.g. fallback plan) still answers from the item field."""
    ctx = _ctx(
        question="Who manages the Finance Department?",
        results=[
            {
                "facet": "org_units",
                "api": "org_units.get_by_id",
                "status": "success",
                "item": {
                    "id": 2,
                    "name": "Finance Department",
                    "code": "FIN",
                    "manager": "Youssef Nabil",
                    "parent": "Executive Office",
                },
            }
        ],
    )
    assert _fallback(ctx, "en") == "Youssef Nabil"


def test_owner_question_matches_owner_field() -> None:
    ctx = _ctx(
        question="Who owns System ABC?",
        results=[{"status": "success", "item": {"name": "System ABC", "owner": "Ahmed Mohamed"}}],
    )
    assert _fallback(ctx, "en") == "Ahmed Mohamed"


def test_count_question() -> None:
    ctx = _ctx(question="How many employees?", results=[{"status": "success", "count": 42}])
    assert _fallback(ctx, "en") == "Count: 42"
    assert _fallback(ctx, "ar") == "العدد: 42"


def test_readable_summary_never_returns_raw_json() -> None:
    """An item with no question-field match yields a readable line, not JSON."""
    ctx = _ctx(
        question="Tell me about the unit",
        results=[{"status": "success", "item": {"id": 2, "name": "Finance Department", "code": "FIN"}}],
    )
    answer = _fallback(ctx, "en")
    assert not answer.lstrip().startswith(("[", "{"))
    assert "Finance Department" in answer


def test_no_data_message() -> None:
    assert _fallback(_ctx(), "en") == "No data available."
    assert _fallback(_ctx(), "ar") == "لا توجد بيانات متاحة."
