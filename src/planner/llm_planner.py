"""LLM-based planner.

Asks the model for a strict-JSON :class:`ExecutionPlan`. Delegates to the
fallback planner if the model errors or returns an empty plan, guaranteeing the
pipeline always has something to execute.
"""

from __future__ import annotations

from src.config.registry import Registry
from src.llm.ollama_client import LLMError, OllamaLLM
from src.models.plan import ExecutionPlan, PlanIntent
from src.observability.logging import get_logger
from src.nodes.conversation import detect_recall_topic
from src.planner.fallback_planner import FallbackPlanner, detect_language
from src.prompts import render

_log = get_logger("planner")


class LLMPlanner:
    """Produces execution plans using the LLM, with a rule-based fallback."""

    def __init__(self, registry: Registry, llm: OllamaLLM) -> None:
        self._registry = registry
        self._llm = llm
        self._fallback = FallbackPlanner(registry)

    async def plan(self, question: str) -> tuple[ExecutionPlan, dict[str, int]]:
        """Return ``(plan, token_usage)`` for the given question.

        The model decides whether the question is a *data* lookup (produces
        steps) or a general turn (no steps). General turns become ``CHAT`` plans
        that the response generator answers conversationally, like a normal
        assistant - no hardcoded greeting/keyword cases.
        """
        # Conversational/meta questions ("what did I ask before?") are answered
        # faithfully from history; skip the LLM for them.
        if detect_recall_topic(question) is not None:
            return self._fallback.plan(question), {}

        language = detect_language(question)
        prompt = render(
            "planner",
            catalog=self._registry.catalog_summary(),
            concepts=self._format_concepts(),
            question=question,
            language=language,
        )
        try:
            # Weak local models often need at most one correction; keep the
            # repair budget small so we degrade fast.
            plan, usage = await self._llm.structured(
                system=prompt,
                user=question,
                schema=ExecutionPlan,
                max_repair=1,
            )
        except LLMError as exc:
            _log.warning("planner_llm_failed_using_fallback", error=str(exc))
            return self._degraded_fallback(question), {}
        except Exception as exc:  # connectivity/timeout/etc -> stay alive
            _log.warning("planner_llm_unavailable_using_fallback", error=str(exc))
            return self._degraded_fallback(question), {}

        # The model explicitly classified this as a general (non-data) turn.
        if plan.intent is PlanIntent.CHAT:
            return self._chat_plan(question, plan.language or language), usage

        # No steps + DATA intent: the model thinks there's nothing to look up.
        # Re-check with the config-driven keyword net (facet synonyms) in case
        # it missed an obvious data question; otherwise treat it as a chat turn.
        if plan.is_empty:
            net = self._fallback.plan(question)
            if not net.is_empty:
                _log.info("planner_empty_keyword_net_recovered")
                return net, usage
            _log.info("planner_empty_using_chat")
            return self._chat_plan(question, plan.language or language), usage

        # Trust the model's language detection but backfill if missing.
        if not plan.language:
            plan.language = language
        return plan, usage

    def _degraded_fallback(self, question: str) -> ExecutionPlan:
        """When the LLM is unavailable, use the keyword net; chat if it finds nothing.

        Without the LLM we cannot hold a free-form conversation, so a non-data
        turn becomes an (empty) ``CHAT`` plan whose graceful, LLM-free reply is
        produced downstream.
        """
        net = self._fallback.plan(question)
        if not net.is_empty:
            return net
        return self._chat_plan(question, detect_language(question))

    @staticmethod
    def _chat_plan(question: str, language: str) -> ExecutionPlan:
        """A stepless plan marking a general conversational turn."""
        return ExecutionPlan(
            goal=question.strip(),
            steps=[],
            language=language,
            intent=PlanIntent.CHAT,
            used_fallback=False,
        )

    def _format_concepts(self) -> str:
        """Render the semantic catalog into a compact prompt fragment."""
        lines: list[str] = []
        for facet_name, semantics in self._registry.semantic.facets.items():
            for cname, concept in semantics.concepts.items():
                target = f" -> {concept.target}" if concept.target else ""
                via = f"field={concept.field}" if concept.field else f"api={concept.api}"
                lines.append(f"- {facet_name}.{cname} ({via}{target}): {concept.description}")
        return "\n".join(lines) if lines else "(none)"
