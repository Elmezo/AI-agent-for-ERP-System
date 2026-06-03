"""Small shared helpers for nodes.

``trace`` and ``errors`` are per-turn lists (no LangGraph reducer) so they reset
each turn. Within a turn, nodes read the current value and append, then return
the full list (replace semantics). The planner resets them at the turn's start.
"""

from __future__ import annotations

from typing import Any

from src.models.state import AgentState


def append_trace(
    state: AgentState, stage: str, elapsed_ms: float, detail: str = ""
) -> list[dict[str, Any]]:
    """Return the trace list with one new entry appended."""
    entries = list(state.get("trace", []))
    entries.append({"stage": stage, "elapsed_ms": elapsed_ms, "detail": detail})
    return entries


def append_errors(state: AgentState, new_errors: list[str]) -> list[str]:
    """Return the errors list with new errors appended."""
    return list(state.get("errors", [])) + list(new_errors)
