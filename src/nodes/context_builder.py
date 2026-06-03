"""Context Builder node.

Relationship resolution can yield a lot of data; this node compresses it into a
compact, answer-focused context. It trims technical fields, replaces raw foreign
keys with their resolved names, summarises large lists, and highlights the
specific values the user's concepts asked for (the "focus"). The Response
Generator then works from this small context instead of raw API payloads.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import ExecutionPlan, PlanIntent
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.nodes.conversation import answer_recall, answer_smalltalk
from src.observability.logging import get_logger

_log = get_logger("node.context_builder")

_MAX_ITEMS = 25
# Fields worth showing even when not display fields; everything scalar is kept
# unless it is a raw FK that already has a resolved name.
_NOISY_FIELDS = {"password", "secret", "token"}


class ContextBuilderNode:
    """Build a compact ``context`` dict for response generation."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Assemble trimmed results + focus values into ``context``."""
        plan = ExecutionPlan(**state["plan"])
        start = time.perf_counter()

        # Conversational/meta turns are answered from short-term history, not the
        # ERP results. Build a focus value so the validator marks the turn "ok".
        if plan.intent is PlanIntent.RECALL:
            return self._build_recall_context(state, plan, start)

        # Social/meta small talk (greetings, thanks, capabilities) is answered
        # deterministically, with capability hints drawn from the registry.
        if plan.intent is PlanIntent.SMALLTALK:
            return self._build_smalltalk_context(state, plan, start)

        results = state.get("resolved_results") or state.get("execution_results", [])

        built_results: list[dict[str, Any]] = []
        focus: list[dict[str, Any]] = []

        for entry in results:
            if entry.get("kind") == "concept_field":
                focus_item = self._build_field_focus(entry, results)
                if focus_item:
                    focus.append(focus_item)
                continue

            result = entry.get("result")
            if not result:
                continue
            summary = self._summarise(entry, result)
            if summary is not None:
                built_results.append(summary)
            if entry.get("focus"):
                focus.append({"concept": entry["focus"], "from_api": entry.get("api_name")})

        context = {
            "goal": plan.goal,
            "question": state["user_input"],
            "language": state.get("language", plan.language),
            "focus": focus,
            "results": built_results,
            "memories": [m.get("content") for m in state.get("retrieved_memories", [])],
        }

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("context_built", results=len(built_results), focus=len(focus))
        return {
            "context": context,
            "trace": append_trace(state, "context_builder", elapsed, f"results={len(built_results)}"),
        }

    # --- helpers ------------------------------------------------------------
    def _build_recall_context(
        self, state: AgentState, plan: ExecutionPlan, start: float
    ) -> dict[str, Any]:
        """Answer a conversational/meta turn from short-term message history."""
        language = state.get("language", plan.language)
        topic = plan.recall_topic or "history"
        answer = answer_recall(state.get("messages", []), topic, language)
        context = {
            "goal": plan.goal,
            "question": state["user_input"],
            "language": language,
            # Surfacing the answer as a focus value makes the validator treat the
            # turn as "ok" and lets the response generator return it verbatim.
            "focus": [{"concept": "recall", "field": topic, "value": answer}],
            "results": [],
            "memories": [m.get("content") for m in state.get("retrieved_memories", [])],
        }
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("context_built", results=0, focus=1, intent="recall")
        return {
            "context": context,
            "trace": append_trace(state, "context_builder", elapsed, f"recall={topic}"),
        }

    def _build_smalltalk_context(
        self, state: AgentState, plan: ExecutionPlan, start: float
    ) -> dict[str, Any]:
        """Answer a social/meta turn deterministically (no ERP, no LLM)."""
        language = state.get("language", plan.language)
        topic = plan.smalltalk_topic or "greeting"
        capabilities = [f.business_name for f in self._deps.registry.facets.values()]
        answer = answer_smalltalk(topic, language, capabilities)
        context = {
            "goal": plan.goal,
            "question": state["user_input"],
            "language": language,
            # Surface as a focus value so the validator treats the turn as "ok"
            # and the response generator returns it verbatim (no LLM call).
            "focus": [{"concept": "smalltalk", "field": topic, "value": answer}],
            "results": [],
            "memories": [m.get("content") for m in state.get("retrieved_memories", [])],
        }
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("context_built", results=0, focus=1, intent="smalltalk")
        return {
            "context": context,
            "trace": append_trace(state, "context_builder", elapsed, f"smalltalk={topic}"),
        }

    def _summarise(self, entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        """Trim one API result into a compact summary block."""
        facet = entry.get("facet")
        status = result.get("status")
        data = result.get("data")
        block: dict[str, Any] = {
            "facet": facet,
            "api": entry.get("api_name"),
            "status": status,
        }
        if status == "error":
            block["error"] = result.get("error")
            return block
        if status == "empty" or data is None:
            block["data"] = None
            return block
        if isinstance(data, list):
            block["count"] = len(data)
            block["items"] = [self._trim(facet, rec) for rec in data[:_MAX_ITEMS]]
            if len(data) > _MAX_ITEMS:
                block["truncated"] = True
        elif isinstance(data, dict):
            block["item"] = self._trim(facet, data)
        else:
            block["value"] = data
        return block

    def _trim(self, facet: str | None, record: dict[str, Any]) -> dict[str, Any]:
        """Keep readable scalar fields; drop raw FKs that have resolved names."""
        if not isinstance(record, dict):
            return record
        facet_def = self._deps.registry.get_facet(facet) if facet else None
        drop: set[str] = set()
        if facet_def:
            for field, rel in facet_def.relationships.items():
                if rel.resolved_name() in record:
                    drop.add(field)  # raw FK replaced by its resolved name
        trimmed: dict[str, Any] = {}
        for key, value in record.items():
            if key in drop or key in _NOISY_FIELDS:
                continue
            if isinstance(value, (dict, list)):
                continue  # nested structures are summarised elsewhere
            trimmed[key] = value
        return trimmed

    def _build_field_focus(
        self, marker: dict[str, Any], results: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Resolve a field-backed concept marker to its readable value."""
        facet = marker.get("facet")
        focus_field = marker.get("focus_field")
        facet_def = self._deps.registry.get_facet(facet) if facet else None
        if facet_def is None or not focus_field:
            return None
        relationship = facet_def.relationships.get(focus_field)
        resolved_name = relationship.resolved_name() if relationship else focus_field

        # Find the resolved record of this facet among the executed results.
        for entry in results:
            if entry.get("facet") != facet:
                continue
            result = entry.get("result")
            if not result or result.get("status") != "success":
                continue
            data = result.get("data")
            record = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else None)
            if isinstance(record, dict) and resolved_name in record:
                return {
                    "concept": marker.get("focus"),
                    "field": focus_field,
                    "value": record[resolved_name],
                }
        return {"concept": marker.get("focus"), "field": focus_field, "value": None}
