"""Clarification logic for ambiguous entity references.

When a search resolves to several plausible records (e.g. "Ahmed" -> three
people), the agent must ask *which one* instead of silently guessing the first
match. This module holds the pure helpers used by the entity resolver (to detect
ambiguity), the context builder (to phrase the question), and the clarification
resolver node (to map the user's reply back to a candidate on the next turn).

Keeping these as small pure functions makes the behaviour deterministic and
trivially testable, and keeps the nodes thin.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from src.graph.dependencies import PipelineDeps
from src.models.state import AgentState
from src.observability.logging import get_logger

_log = get_logger("node.clarification")

# Cap how many options we present so the question stays readable.
MAX_CANDIDATES = 6
# Fields used to distinguish same-named records, in priority order.
_HINT_FIELDS = ("title", "role", "description", "code", "status", "email")

# Arabic ordinal words -> 1-based index.
_AR_ORDINALS: dict[str, int] = {
    "الأول": 1, "الاول": 1, "اول": 1,
    "الثاني": 2, "الثانى": 2, "ثاني": 2,
    "الثالث": 3, "ثالث": 3,
    "الرابع": 4, "رابع": 4,
    "الخامس": 5, "خامس": 5,
    "السادس": 6, "سادس": 6,
}
_EN_ORDINALS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
}


class _Namer(Protocol):
    """Anything able to build a record's display label (the FacetService)."""

    def display_name(self, facet: str, record: dict[str, Any]) -> str: ...


def disambiguate(
    facet: str, query: str, records: list[dict[str, Any]], namer: _Namer
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Pick a record, or report ambiguity.

    Returns ``(record, None)`` when a single record is the clear answer, or
    ``(None, candidates)`` when the search is ambiguous and the user should be
    asked. ``candidates`` are compact ``{id, label, hint}`` dicts.
    """
    if len(records) <= 1:
        return (records[0] if records else None), None

    needle = query.strip().lower()
    exact = [r for r in records if _is_exact(facet, r, needle, namer)]
    if len(exact) == 1:
        return exact[0], None

    candidates = [_candidate(facet, r, namer) for r in records[:MAX_CANDIDATES]]
    return None, candidates


def build_clarification(
    facet: str, query: str, original_question: str, candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assemble the clarification payload stored in state."""
    return {
        "needed": True,
        "facet": facet,
        "query": query,
        "original_question": original_question,
        "candidates": candidates,
    }


def format_clarification(clarification: dict[str, Any], language: str) -> str:
    """Render the clarification as a localized, numbered question."""
    is_ar = str(language).startswith("ar")
    query = clarification.get("query", "")
    candidates = clarification.get("candidates", [])
    head = (
        f'وجدت أكثر من نتيجة مطابقة لـ "{query}". أيهم تقصد؟' if is_ar
        else f'I found multiple matches for "{query}". Which one do you mean?'
    )
    lines = [head]
    for i, cand in enumerate(candidates, start=1):
        hint = cand.get("hint")
        suffix = f" — {hint}" if hint else ""
        lines.append(f"{i}. {cand.get('label')}{suffix}")
    return "\n".join(lines)


def parse_selection(
    user_input: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Map the user's reply to one candidate, or ``None`` if it isn't a choice.

    Accepts an ordinal ("1", "#2", "first", "الأول") or a name fragment that
    uniquely identifies a candidate ("Mohamed", "Ahmed Mohamed").
    """
    if not candidates:
        return None
    text = (user_input or "").strip().lower()
    if not text:
        return None

    index = _parse_ordinal(text)
    if index is not None and 1 <= index <= len(candidates):
        return candidates[index - 1]

    # Unique name-fragment match (avoid matching when several still apply).
    matches = [c for c in candidates if text in str(c.get("label", "")).lower()]
    if len(matches) == 1:
        return matches[0]
    # Try the reverse: the user typed a longer phrase containing exactly one label.
    contained = [c for c in candidates if str(c.get("label", "")).lower() in text]
    if len(contained) == 1:
        return contained[0]
    return None


def rewrite_question(original_question: str, query: str, chosen_label: str) -> str:
    """Replace the ambiguous term in the original question with the chosen label."""
    if query:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        if pattern.search(original_question):
            return pattern.sub(chosen_label, original_question, count=1)
    return f"{original_question} ({chosen_label})"


# --- internals -------------------------------------------------------------
def _is_exact(facet: str, record: dict[str, Any], needle: str, namer: _Namer) -> bool:
    """True when the record's label or name equals the query (case-insensitive)."""
    label = namer.display_name(facet, record).strip().lower()
    name = str(record.get("name", "")).strip().lower()
    return needle in (label, name) and needle != ""


def _candidate(facet: str, record: dict[str, Any], namer: _Namer) -> dict[str, Any]:
    """Build a compact candidate dict with a distinguishing hint."""
    return {
        "id": record.get("id"),
        "label": namer.display_name(facet, record),
        "hint": _hint(record),
    }


def _hint(record: dict[str, Any]) -> str:
    """Pick a short distinguishing detail (title, code, status, ...)."""
    for field in _HINT_FIELDS:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


def _parse_ordinal(text: str) -> int | None:
    """Extract a 1-based selection index from ordinal text, if present."""
    match = re.match(r"^#?\s*(\d+)", text)
    if match:
        return int(match.group(1))
    for word, idx in {**_EN_ORDINALS, **_AR_ORDINALS}.items():
        if word in text:
            return idx
    return None


class ClarificationResolverNode:
    """Resolve a pending clarification from the user's reply (runs first).

    If the previous turn asked the user to choose among candidates and this
    turn's message is a valid selection, rewrite the original question with the
    chosen entity so the normal pipeline answers it. Otherwise clear the pending
    clarification and treat the message as a new question.
    """

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Rewrite the question when the reply selects a pending candidate."""
        pending = state.get("clarification") or {}
        if not pending.get("needed"):
            return {}

        candidates = pending.get("candidates", [])
        chosen = parse_selection(state.get("user_input", ""), candidates)
        if chosen is None:
            # Not a selection: drop the stale clarification, proceed normally.
            _log.info("clarification_abandoned")
            return {"clarification": {"needed": False}}

        rewritten = rewrite_question(
            pending.get("original_question", ""), pending.get("query", ""),
            str(chosen.get("label", "")),
        )
        _log.info("clarification_resolved", choice=chosen.get("label"), question=rewritten)
        return {"user_input": rewritten, "clarification": {"needed": False}}
