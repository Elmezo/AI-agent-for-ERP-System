"""Response Generator node.

Produces the final natural-language answer in the user's language.

Routing by intent/verdict:
- ``CHAT`` turns (anything that is not a data lookup) are answered
  conversationally by the LLM using the chat history - like a normal assistant.
- ``RECALL`` turns return the deterministic answer the context builder computed.
- ``ok`` data turns ask the LLM to summarise the compact context (facts only).
- ``empty`` / ``error`` verdicts use the validator's deterministic, localized
  message (no LLM, no hallucination risk).
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.plan import PlanIntent
from src.models.state import AgentState
from src.models.web import WebSearchDecision, WebSearchResult
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
        intent = (state.get("plan") or {}).get("intent")

        if intent == PlanIntent.CHAT.value:
            # Not a data question: respond like a general chat assistant.
            answer, usage = await self._chat(state, language)
        elif (verbatim := self._deterministic_answer(state)) is not None:
            # RECALL turns carry a pre-computed, faithful answer; return it
            # directly instead of risking LLM paraphrasing.
            answer = verbatim
        elif status != "ok":
            answer = validation.get("message") or ""
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
        """Return a pre-computed answer for ``RECALL`` turns, else ``None``.

        ``RECALL`` turns are answered deterministically by the context builder
        (stored as the single focus value). Returning it verbatim keeps the
        recall answer faithful and LLM-independent.
        """
        intent = (state.get("plan") or {}).get("intent")
        if intent != PlanIntent.RECALL.value:
            return None
        for item in state.get("context", {}).get("focus", []):
            value = item.get("value")
            if value not in (None, ""):
                return str(value)
        return None

    async def _chat(self, state: AgentState, language: str) -> tuple[str, dict[str, int]]:
        """Answer a general (non-data) turn conversationally, using chat history.

        Like a browsing assistant: if the question needs current or external
        knowledge (and web search is enabled), search the web first and ground
        the reply in the results.
        """
        capabilities = ", ".join(
            f.business_name for f in self._deps.registry.facets.values()
        ) or "company data"
        question = state["user_input"]

        web_context, web_usage = await self._maybe_search_web(question, language)

        prompt = render(
            "chat",
            language=language,
            capabilities=capabilities,
            web_context=web_context,
        )
        history = state.get("messages", [])
        try:
            text, usage = await self._deps.llm.chat(system=prompt, history=history)
        except Exception as exc:  # never crash the turn on LLM failure
            _log.warning("chat_llm_failed", error=str(exc))
            return self._chat_fallback(language), web_usage
        merged = {k: web_usage.get(k, 0) + usage.get(k, 0) for k in set(web_usage) | set(usage)}
        return text.strip() or self._chat_fallback(language), merged

    async def _maybe_search_web(
        self, question: str, language: str
    ) -> tuple[str, dict[str, int]]:
        """Decide whether to search the web and, if so, return a context block.

        Returns ``(web_context, token_usage)`` where ``web_context`` is an empty
        string when search is disabled, unnecessary, or unsuccessful.
        """
        service = self._deps.web_search
        if service is None:  # web search not configured
            return "", {}

        decision, usage = await self._decide_web_search(question)
        if not decision.needs_search:
            return "", usage

        query = decision.query.strip() or question
        result = await service.search(query)
        if not result.ok:
            _log.info("web_search_no_results", query=query[:120], error=result.error)
            return "", usage
        _log.info("web_search_used", query=query[:120], sources=len(result.results))
        return self._format_web_context(result, language), usage

    async def _decide_web_search(self, question: str) -> tuple[WebSearchDecision, dict[str, int]]:
        """Ask the LLM (structured) whether this turn needs a web search."""
        prompt = render("web_decision", question=question)
        try:
            decision, usage = await self._deps.llm.structured(
                system=prompt,
                user=question,
                schema=WebSearchDecision,
                max_repair=1,
            )
            return decision, usage
        except Exception as exc:  # default to no-search on any failure
            _log.warning("web_decision_failed", error=str(exc))
            return WebSearchDecision(needs_search=False), {}

    @staticmethod
    def _format_web_context(result: WebSearchResult, language: str) -> str:
        """Render search findings into a prompt block the LLM must ground on."""
        lines = [
            "## Web search results (use these to answer; cite source URLs)",
        ]
        if result.answer:
            lines.append(f"Summary: {result.answer}")
        for item in result.results[:5]:
            snippet = item.content.strip().replace("\n", " ")
            if len(snippet) > 500:
                snippet = snippet[:500] + "..."
            lines.append(f"- {item.title} ({item.url}): {snippet}")
        instruction = (
            "اعتمد على نتائج البحث أعلاه في إجابتك واذكر المصادر عند الحاجة."
            if str(language).startswith("ar")
            else "Base your answer on the search results above and mention sources where helpful."
        )
        lines.append(instruction)
        return "\n".join(lines)

    @staticmethod
    def _chat_fallback(language: str) -> str:
        """Minimal reply when the LLM is unavailable for a chat turn."""
        is_ar = str(language).startswith("ar")
        return (
            "مرحباً! كيف يمكنني مساعدتك؟" if is_ar
            else "Hello! How can I help you?"
        )

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
