"""LLM-based planner.

Asks the model for a strict-JSON :class:`ExecutionPlan`. Delegates to the
fallback planner if the model errors or returns an empty plan, guaranteeing the
pipeline always has something to execute.
"""

from __future__ import annotations

from src.config.registry import Registry
from src.llm.ollama_client import LLMError, OllamaLLM
from src.models.plan import ExecutionPlan
from src.observability.logging import get_logger
from src.nodes.conversation import detect_recall_topic, detect_smalltalk
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
        """Return ``(plan, token_usage)`` for the given question."""
        # Conversational/meta questions ("what did I ask before?") and social
        # small talk ("hi", "thanks") are handled deterministically; skip the
        # LLM entirely for them.
        if detect_recall_topic(question) is not None or detect_smalltalk(question) is not None:
            return self._fallback.plan(question), {}

        prompt = render(
            "planner",
            catalog=self._registry.catalog_summary(),
            concepts=self._format_concepts(),
            question=question,
            language=detect_language(question),
        )
        try:
            # Weak local models often need at most one correction; keep the
            # repair budget small so we fall back to the rule-based planner fast.
            plan, usage = await self._llm.structured(
                system=prompt,
                user=question,
                schema=ExecutionPlan,
                max_repair=1,
            )
        except LLMError as exc:
            _log.warning("planner_llm_failed_using_fallback", error=str(exc))
            return self._fallback.plan(question), {}
        except Exception as exc:  # connectivity/timeout/etc -> stay alive
            _log.warning("planner_llm_unavailable_using_fallback", error=str(exc))
            return self._fallback.plan(question), {}

        if plan.is_empty:
            _log.info("planner_empty_using_fallback")
            fallback = self._fallback.plan(question)
            return fallback, usage

        # Trust the model's language detection but backfill if missing.
        if not plan.language:
            plan.language = detect_language(question)
        return plan, usage

    def _format_concepts(self) -> str:
        """Render the semantic catalog into a compact prompt fragment."""
        lines: list[str] = []
        for facet_name, semantics in self._registry.semantic.facets.items():
            for cname, concept in semantics.concepts.items():
                target = f" -> {concept.target}" if concept.target else ""
                via = f"field={concept.field}" if concept.field else f"api={concept.api}"
                lines.append(f"- {facet_name}.{cname} ({via}{target}): {concept.description}")
        return "\n".join(lines) if lines else "(none)"
