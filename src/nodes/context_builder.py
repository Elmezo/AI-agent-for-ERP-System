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
from src.models.analytics import AnalyticsResult
from src.models.plan import ExecutionPlan, PlanIntent
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.nodes.conversation import answer_recall
from src.observability.logging import get_logger

_log = get_logger("node.context_builder")

_MAX_ITEMS = 25
# Fields worth showing even when not display fields; everything scalar is kept
# unless it is a raw FK that already has a resolved name.
_NOISY_FIELDS = {"password", "secret", "token"}


def _fmt_num(value: float) -> str:
    """Format a number readably: integers without decimals, both with separators."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{round(value, 2):,}"


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

        # Analytics ("SQL mode") turns: surface the computed answer deterministically.
        analytics = state.get("analytics") or []
        if analytics:
            return self._build_analytics_context(state, plan, analytics, start)

        results = state.get("resolved_results") or state.get("execution_results", [])
        consumed_lists = self._consumed_list_steps(plan)

        built_results: list[dict[str, Any]] = []
        focus: list[dict[str, Any]] = []

        for entry in results:
            if entry.get("kind") == "concept_field":
                focus_item = self._build_field_focus(entry, results)
                if focus_item:
                    focus.append(focus_item)
                continue

            # A raw list consumed by a join/aggregate is an intermediate; its
            # meaningful subset is surfaced by the join output instead.
            if entry.get("kind") == "list" and entry.get("step_id") in consumed_lists:
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
    @staticmethod
    def _consumed_list_steps(plan: ExecutionPlan) -> set[int]:
        """Step ids of list results that feed a join or aggregate (intermediate).

        These are hidden from the final context so the answer shows the entity
        plus the *matched* rows (the join output), not the full raw list.
        """
        consumed: set[int] = set()
        for step in plan.steps:
            if step.join is not None:
                consumed.add(step.join.left_step)
                consumed.add(step.join.right_step)
            if step.aggregate is not None:
                consumed.update(step.depends_on)
        return consumed

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

    def _build_analytics_context(
        self,
        state: AgentState,
        plan: ExecutionPlan,
        analytics: list[dict[str, Any]],
        start: float,
    ) -> dict[str, Any]:
        """Turn computed analytics into a deterministic, readable answer.

        The formatted answer is surfaced as a focus value so the validator marks
        the turn "ok" and the response generator returns the exact numbers
        verbatim (no LLM paraphrasing of figures).
        """
        language = state.get("language", plan.language)
        results = [AnalyticsResult(**r) for r in analytics]
        answer = "\n\n".join(self._format_analytics(r, language) for r in results)
        context = {
            "goal": plan.goal,
            "question": state["user_input"],
            "language": language,
            "focus": [{"concept": "analytics", "field": "result", "value": answer}],
            "analytics": analytics,
            "results": [],
            "memories": [m.get("content") for m in state.get("retrieved_memories", [])],
        }
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("context_built", results=0, focus=1, intent="analytics")
        return {
            "context": context,
            "trace": append_trace(state, "context_builder", elapsed, f"analytics={len(results)}"),
        }

    # --- analytics formatting ----------------------------------------------
    def _format_analytics(self, result: AnalyticsResult, language: str) -> str:
        """Render one analytics result as a localized, human-readable answer."""
        is_ar = str(language).startswith("ar")
        business = self._facet_label(result.facet)

        if result.error:
            return (
                f"تعذّر حساب ذلك: {result.error}." if is_ar
                else f"I couldn't compute that: {result.error}."
            )

        if result.groups:
            return self._format_groups(result, business, is_ar)

        if result.op == "count":
            label = "العدد" if is_ar else business
        else:
            label = self._metric_label(result, is_ar)
        value = _fmt_num(result.value) if result.value is not None else ("لا يوجد" if is_ar else "n/a")
        return f"{label}: {value}"

    def _format_groups(self, result: AnalyticsResult, business: str, is_ar: bool) -> str:
        """Render grouped or top-N results as a titled list."""
        if result.group_by:
            header = (
                f"{business} حسب {result.group_by}:" if is_ar
                else f"{business} by {result.group_by}:"
            )
        else:
            n = len(result.groups)
            header = (
                f"أعلى {n} {business} حسب {result.metric}:" if is_ar
                else f"Top {n} {business} by {result.metric}:"
            )
        lines = [f"- {g.key}: {_fmt_num(g.value)}" for g in result.groups]
        return "\n".join([header, *lines])

    def _metric_label(self, result: AnalyticsResult, is_ar: bool) -> str:
        """Build the scalar-answer label (e.g. 'Average budget')."""
        op, metric = result.op, result.metric or ""
        labels_ar = {
            "count": "العدد", "sum": f"إجمالي {metric}", "avg": f"متوسط {metric}",
            "min": f"أدنى {metric}", "max": f"أعلى {metric}",
        }
        labels_en = {
            "count": "Count", "sum": f"Total {metric}", "avg": f"Average {metric}",
            "min": f"Minimum {metric}", "max": f"Maximum {metric}",
        }
        return (labels_ar if is_ar else labels_en).get(op, op)

    def _facet_label(self, facet: str) -> str:
        """Human-friendly facet name from the registry, falling back to the key."""
        facet_def = self._deps.registry.get_facet(facet)
        return facet_def.business_name if facet_def else facet

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
