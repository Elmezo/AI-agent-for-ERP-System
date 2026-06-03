"""Memory Manager node.

Final stage. Applies the memory policy: *do not save every conversation* - only
persist turns that produced a usable answer and look worth remembering. Saving
is best-effort and never fails the turn.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.memory.repository import MemoryRecord
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.observability.logging import get_logger

_log = get_logger("node.memory_manager")

# Minimum answer length (chars) considered "worth remembering".
_MIN_USEFUL_CHARS = 8


class MemoryManagerNode:
    """Persist useful memories at the end of a successful turn."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Evaluate importance and conditionally store a memory."""
        start = time.perf_counter()
        validation = state.get("validation", {})
        answer = state.get("final_response", "")
        thread_id = state.get("thread_id", "default")
        saved = False

        if self._should_save(validation, answer):
            try:
                await self._deps.memory.add(
                    MemoryRecord(
                        thread_id=thread_id,
                        content=f"Q: {state['user_input']}\nA: {answer}",
                        kind="insight",
                        importance=self._importance(state),
                        metadata={"goal": state.get("context", {}).get("goal", "")},
                    )
                )
                saved = True
            except Exception as exc:  # memory is auxiliary
                _log.warning("memory_save_failed", error=str(exc))

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("memory_evaluated", saved=saved)
        return {
            "trace": append_trace(state, "memory_manager", elapsed, f"saved={saved}"),
        }

    @staticmethod
    def _should_save(validation: dict[str, Any], answer: str) -> bool:
        """Only remember successful, non-trivial answers."""
        return validation.get("status") == "ok" and len(answer.strip()) >= _MIN_USEFUL_CHARS

    @staticmethod
    def _importance(state: AgentState) -> float:
        """Heuristic importance: focused, concept-driven answers rank higher."""
        focus = state.get("context", {}).get("focus", [])
        return 0.7 if focus else 0.4
