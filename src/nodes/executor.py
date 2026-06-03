"""Executor node.

Runs the concrete API calls produced by the API Selector and normalises every
outcome into an :class:`ApiResult`. Concept *focus markers* (field-backed) carry
no API call and are passed through untouched for later stages. Errors are
captured, never raised, so the pipeline can report problems gracefully.
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.state import AgentState
from src.nodes._helpers import append_errors, append_trace
from src.observability.logging import get_logger

_log = get_logger("node.executor")


class ExecutorNode:
    """Execute selected API calls and collect normalised results."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Invoke each selected call and gather results + focus markers."""
        selected = state.get("selected_apis", [])
        start = time.perf_counter()
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        api_calls: list[str] = []

        for entry in selected:
            api_name = entry.get("api_name")
            if not api_name:
                # Field-backed concept focus: nothing to execute, keep as marker.
                results.append({**entry, "result": None})
                continue

            result = await self._deps.client.call(
                api_name,
                path_params=entry.get("path_params") or {},
                query_params=entry.get("query_params") or {},
            )
            api_calls.append(api_name)
            if result.is_error:
                errors.append(f"{api_name}: {result.error}")
            results.append(
                {
                    "step_id": entry.get("step_id"),
                    "kind": entry.get("kind"),
                    "facet": entry.get("facet"),
                    "api_name": api_name,
                    "focus": entry.get("focus"),
                    "result": result.model_dump(mode="json"),
                }
            )

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("executed", calls=len(api_calls), errors=len(errors))
        update: dict[str, Any] = {
            "execution_results": results,
            "trace": append_trace(state, "executor", elapsed, f"calls={api_calls} errors={len(errors)}"),
        }
        if errors:
            update["errors"] = append_errors(state, errors)
        return update
