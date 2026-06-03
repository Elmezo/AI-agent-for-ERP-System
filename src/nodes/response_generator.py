"""Response Generator node.

Produces the final natural-language answer in the user's language. For ``empty``
/ ``error`` / ``no_plan`` verdicts it uses the validator's deterministic,
localized message (no LLM, no hallucination risk). For ``ok`` it asks the LLM to
summarise the compact context, instructed to use only the provided facts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import PlanIntent
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.observability.logging import get_logger
from src.prompts import render

_log = get_logger("node.response_generator")


class ResponseGeneratorNode:
    """Generate the user-facing answer."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Return ``final_response`` and append the assistant message."""
        start = time.perf_counter()
        validation = state.get("validation", {"status": "no_plan", "message": ""})
        language = state.get("language", "en")
        status = validation.get("status")

        usage: dict[str, int] = {}
        if status != "ok":
            answer = validation.get("message") or ""
        elif (verbatim := self._deterministic_answer(state)) is not None:
            # Meta turns (recall/small talk) carry a pre-computed, faithful
            # answer; return it directly instead of risking LLM paraphrasing.
            answer = verbatim
        else:
            answer, usage = await self._generate(state, language)

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("response_generated", status=status, chars=len(answer))
        return {
            "final_response": answer,
            "messages": [{"role": "assistant", "content": answer}],
            "trace": append_trace(
                state, "response_generator", elapsed,
                f"status={status} tokens={usage.get('completion', 0)}",
            ),
        }

    @staticmethod
    def _deterministic_answer(state: AgentState) -> str | None:
        """Return a pre-computed answer for non-data turns, else ``None``.

        ``RECALL`` and ``SMALLTALK`` turns are answered deterministically by the
        context builder (stored as the single focus value). Returning it verbatim
        keeps these meta answers faithful and LLM-independent.
        """
        intent = (state.get("plan") or {}).get("intent")
        if intent not in (PlanIntent.RECALL.value, PlanIntent.SMALLTALK.value):
            return None
        for item in state.get("context", {}).get("focus", []):
            value = item.get("value")
            if value not in (None, ""):
                return str(value)
        return None

    async def _generate(self, state: AgentState, language: str) -> tuple[str, dict[str, int]]:
        """Call the LLM to summarise the validated context."""
        context = state.get("context", {})
        context_json = json.dumps(context, ensure_ascii=False, indent=2, default=str)
        prompt = render(
            "response",
            language=language,
            question=state["user_input"],
            context=context_json,
        )
        try:
            text, usage = await self._deps.llm.complete(system=prompt, user=state["user_input"])
        except Exception as exc:  # never crash the turn on LLM failure
            _log.warning("response_llm_failed", error=str(exc))
            return self._fallback_answer(context, language), {}
        return text.strip() or self._fallback_answer(context, language), usage

    @classmethod
    def _fallback_answer(cls, context: dict[str, Any], language: str) -> str:
        """Deterministic, human-readable answer used when the LLM is unavailable.

        Resolution order (most specific first):
          1. Concept ``focus`` values the plan explicitly asked for.
          2. A field on the result item whose meaning matches the question
             (e.g. "who manages ..." -> the ``manager`` field).
          3. A count, when the question is a counting question.
          4. A readable ``key: value`` summary of the item(s).
          5. A polite "no data" message.

        It never emits raw JSON, so the user always gets a sentence-like answer.
        """
        is_ar = str(language).startswith("ar")
        question = str(context.get("question") or "").lower()
        results = context.get("results", [])

        # 1) Explicit concept focus values (e.g. resolved manager/owner name).
        focus_values = [
            str(item["value"]).strip()
            for item in context.get("focus", [])
            if item.get("value") not in (None, "")
        ]
        if focus_values:
            return "، ".join(focus_values) if is_ar else ", ".join(focus_values)

        # 2) Match the question intent to a specific field on a single item.
        matched = cls._match_field_from_question(question, results)
        if matched is not None:
            return matched

        # 3) Counting questions.
        for block in results:
            if block.get("count") is not None:
                return f"العدد: {block['count']}" if is_ar else f"Count: {block['count']}"

        # 4) Readable summary of the returned item(s) (never raw JSON).
        summary = cls._readable_summary(results, is_ar)
        if summary:
            return summary

        # 5) Nothing usable.
        return "لا توجد بيانات متاحة." if is_ar else "No data available."

    # Maps a resolved field name on a record to the words that ask about it.
    _FIELD_HINTS: dict[str, tuple[str, ...]] = {
        "manager": ("manage", "manages", "managed", "managing", "manager",
                    "head of", "led by", "leads", "مدير", "يدير", "رئيس"),
        "owner": ("owner", "owns", "own", "responsible", "مالك", "صاحب", "مسؤول", "مسئول"),
        "creator": ("creator", "created", "create", "author", "made by", "منشئ", "أنشأ", "انشأ"),
        "parent": ("parent", "belongs to", "under", "part of", "الأصل", "التابع", "ينتمي"),
        "department": ("department", "org unit", "unit", "قسم", "إدارة", "ادارة", "وحدة"),
        "orgUnit": ("department", "org unit", "unit", "قسم", "إدارة", "ادارة", "وحدة"),
    }

    @classmethod
    def _match_field_from_question(
        cls, question: str, results: list[dict[str, Any]]
    ) -> str | None:
        """Return the value of the item field the question is asking about, if any."""
        for item in cls._iter_items(results):
            for field, triggers in cls._FIELD_HINTS.items():
                value = item.get(field)
                if value in (None, ""):
                    continue
                if any(word in question for word in triggers):
                    return str(value)
        return None

    @staticmethod
    def _iter_items(results: list[dict[str, Any]]):
        """Yield each scalar record contained in the compact result blocks."""
        for block in results:
            if isinstance(block.get("item"), dict):
                yield block["item"]
            for rec in block.get("items", []) or []:
                if isinstance(rec, dict):
                    yield rec

    _LABEL_KEYS = ("name", "title", "fullName", "code")

    @classmethod
    def _readable_summary(cls, results: list[dict[str, Any]], is_ar: bool) -> str:
        """Build a short, readable summary of the first item(s); never raw JSON."""
        items = list(cls._iter_items(results))
        if not items:
            return ""
        lines: list[str] = []
        for item in items[:5]:
            pairs = [
                f"{key}: {value}"
                for key, value in item.items()
                if value not in (None, "") and not str(key).lower().endswith("id")
            ]
            if not pairs:
                continue
            label = next((str(item[k]) for k in cls._LABEL_KEYS if item.get(k)), None)
            lines.append(f"• {label} — {'; '.join(pairs)}" if label else f"• {'; '.join(pairs)}")
        return "\n".join(lines)
