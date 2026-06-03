"""Tests for the clarification (entity disambiguation) helpers and node."""

from __future__ import annotations

from typing import Any

from src.nodes.clarification import (
    ClarificationResolverNode,
    build_clarification,
    disambiguate,
    format_clarification,
    parse_selection,
    rewrite_question,
)


class _Namer:
    """Stub that mirrors FacetService.display_name (uses the ``name`` field)."""

    def display_name(self, facet: str, record: dict[str, Any]) -> str:
        return str(record.get("name", record.get("id")))


_AHMEDS = [
    {"id": 1, "name": "Ahmed Mohamed", "title": "CTO"},
    {"id": 8, "name": "Ahmed Ali", "title": "Sales Manager"},
    {"id": 9, "name": "Ahmed Hassan", "title": "IT Support"},
]


# --- disambiguate ----------------------------------------------------------
def test_single_record_is_unambiguous() -> None:
    record, candidates = disambiguate("people", "Ahmed", [_AHMEDS[0]], _Namer())
    assert candidates is None
    assert record["id"] == 1


def test_exact_match_wins_over_partials() -> None:
    # "Ahmed Mohamed" matches one record exactly -> no clarification.
    record, candidates = disambiguate("people", "Ahmed Mohamed", _AHMEDS, _Namer())
    assert candidates is None
    assert record["id"] == 1


def test_multiple_partial_matches_are_ambiguous() -> None:
    record, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    assert record is None
    assert candidates is not None
    assert [c["id"] for c in candidates] == [1, 8, 9]
    assert candidates[0]["hint"] == "CTO"  # distinguishing detail attached


def test_no_records_returns_nothing() -> None:
    record, candidates = disambiguate("people", "Nobody", [], _Namer())
    assert record is None
    assert candidates is None


# --- formatting ------------------------------------------------------------
def test_format_clarification_english_numbered() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    clar = build_clarification("people", "Ahmed", "Show Ahmed's projects", candidates)
    text = format_clarification(clar, "en")
    assert "Which one do you mean?" in text
    assert "1. Ahmed Mohamed — CTO" in text
    assert "2. Ahmed Ali — Sales Manager" in text
    assert "3. Ahmed Hassan — IT Support" in text


def test_format_clarification_arabic() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    clar = build_clarification("people", "Ahmed", "مشاريع أحمد", candidates)
    text = format_clarification(clar, "ar")
    assert "أيهم تقصد؟" in text
    assert "1. Ahmed Mohamed" in text


# --- selection parsing -----------------------------------------------------
def test_parse_selection_by_ordinal() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    assert parse_selection("2", candidates)["id"] == 8
    assert parse_selection("#3", candidates)["id"] == 9
    assert parse_selection("first", candidates)["id"] == 1
    assert parse_selection("الثاني", candidates)["id"] == 8


def test_parse_selection_by_unique_name_fragment() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    assert parse_selection("Hassan", candidates)["id"] == 9
    assert parse_selection("Ahmed Mohamed", candidates)["id"] == 1


def test_parse_selection_rejects_ambiguous_and_empty() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    assert parse_selection("Ahmed", candidates) is None  # matches all three
    assert parse_selection("", candidates) is None
    assert parse_selection("9", candidates) is None  # out of range


# --- question rewriting ----------------------------------------------------
def test_rewrite_substitutes_term_case_insensitive() -> None:
    assert rewrite_question("Show Ahmed's projects", "Ahmed", "Ahmed Mohamed") == (
        "Show Ahmed Mohamed's projects"
    )


def test_rewrite_falls_back_to_appending_label() -> None:
    assert rewrite_question("their projects", "Ahmed", "Ahmed Ali") == (
        "their projects (Ahmed Ali)"
    )


# --- resolver node ---------------------------------------------------------
async def test_resolver_rewrites_on_valid_selection() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    state = {
        "user_input": "1",
        "clarification": build_clarification(
            "people", "Ahmed", "Show Ahmed's projects", candidates
        ),
    }
    out = await ClarificationResolverNode(deps=None)(state)
    assert out["user_input"] == "Show Ahmed Mohamed's projects"
    assert out["clarification"]["needed"] is False


async def test_resolver_clears_pending_on_non_selection() -> None:
    _, candidates = disambiguate("people", "Ahmed", _AHMEDS, _Namer())
    state = {
        "user_input": "actually, list all systems",
        "clarification": build_clarification(
            "people", "Ahmed", "Show Ahmed's projects", candidates
        ),
    }
    out = await ClarificationResolverNode(deps=None)(state)
    assert "user_input" not in out  # question left untouched
    assert out["clarification"]["needed"] is False


async def test_resolver_noop_without_pending() -> None:
    out = await ClarificationResolverNode(deps=None)({"user_input": "hello"})
    assert out == {}
