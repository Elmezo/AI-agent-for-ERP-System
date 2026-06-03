"""Planner node.

First stage of the pipeline. It (1) retrieves relevant long-term memories and
(2) produces a structured :class:`ExecutionPlan` (LLM with rule-based fallback).
It does not call business APIs or answer the question.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.state import AgentState
from src.observability.logging import get_logger

_log = get_logger("node.planner")


class PlannerNode:
    """Build the execution plan and load memory context."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Plan the run and attach retrieved memories + the user message."""
        question = state["user_input"]
        thread_id = state.get("thread_id", "default")
        start = time.perf_counter()

        memories = await self._retrieve_memories(thread_id, question)
        plan, usage = await self._deps.planner.plan(question)

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info(
            "planned",
            steps=len(plan.steps),
            fallback=plan.used_fallback,
            language=plan.language,
        )
        # Planner runs first each turn, so it RESETS the per-turn trace + errors.
        detail = (
            f"steps={len(plan.steps)} fallback={plan.used_fallback} "
            f"tokens={usage.get('completion', 0)}"
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "language": plan.language,
            "retrieved_memories": memories,
            "messages": [{"role": "user", "content": question}],
            "errors": [],
            "trace": [{"stage": "planner", "elapsed_ms": elapsed, "detail": detail}],
        }

    async def _retrieve_memories(self, thread_id: str, question: str) -> list[dict[str, Any]]:
        """Best-effort memory retrieval; never fails the run."""
        try:
            records = await self._deps.memory.search(thread_id, question, limit=5)
        except Exception as exc:  # memory is auxiliary; degrade gracefully
            _log.warning("memory_retrieval_failed", error=str(exc))
            return []
        return [{"content": r.content, "kind": r.kind} for r in records]
