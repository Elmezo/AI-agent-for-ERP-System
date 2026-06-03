"""Response Validator node.

Classifies the gathered context into one of: ``ok`` (usable data), ``empty``
(queries succeeded but returned nothing), ``error`` (calls failed), or
``no_plan`` (nothing to execute). This is what prevents the agent from reporting
a misleading "0" when the truth is "no data" or "an error occurred".
"""

from __future__ import annotations

import time
from typing import Any

from src.graph.dependencies import PipelineDeps
from src.models.state import AgentState
from src.nodes._helpers import append_trace
from src.observability.logging import get_logger

_log = get_logger("node.response_validator")

_MESSAGES = {
    "empty": {
        "ar": "لا توجد بيانات مطابقة لطلبك حالياً.",
        "en": "No matching data was found for your request.",
    },
    "error": {
        "ar": "حدثت مشكلة أثناء جلب البيانات من النظام. يرجى المحاولة مرة أخرى.",
        "en": "There was a problem retrieving the data from the system. Please try again.",
    },
    "no_plan": {
        "ar": "لم أتمكن من فهم سؤالك أو ربطه بالبيانات المتاحة. هل يمكنك إعادة صياغته؟",
        "en": "I couldn't map your question to the available data. Could you rephrase it?",
    },
}


class ResponseValidatorNode:
    """Decide whether we have data, nothing, or an error - and localise it."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        """Produce a ``validation`` verdict from the built context."""
        start = time.perf_counter()
        context = state.get("context", {})
        language = state.get("language", "en")
        results = context.get("results", [])
        focus = context.get("focus", [])

        has_data = any(self._block_has_data(b) for b in results)
        has_focus_value = any(f.get("value") not in (None, "") for f in focus)
        has_error = any(b.get("status") == "error" for b in results)
        has_any_call = bool(results) or bool(focus)

        if has_data or has_focus_value:
            status = "ok"
        elif not has_any_call:
            status = "no_plan"
        elif has_error and not self._any_empty(results):
            status = "error"
        elif has_error:
            # Some calls errored, others returned empty -> surface the error.
            status = "error"
        else:
            status = "empty"

        message = "" if status == "ok" else self._localise(status, language)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        _log.info("validated", status=status, has_data=has_data)
        return {
            "validation": {"status": status, "message": message, "has_data": has_data},
            "trace": append_trace(state, "response_validator", elapsed, status),
        }

    @staticmethod
    def _block_has_data(block: dict[str, Any]) -> bool:
        """True when a result block carries usable data."""
        if block.get("status") != "success":
            return False
        if "count" in block:
            return block["count"] > 0
        return block.get("item") is not None or block.get("value") is not None

    @staticmethod
    def _any_empty(results: list[dict[str, Any]]) -> bool:
        """True when any result block is an empty (but successful) response."""
        return any(b.get("status") == "empty" or (b.get("count") == 0) for b in results)

    @staticmethod
    def _localise(status: str, language: str) -> str:
        """Return a localized message for a non-ok status."""
        lang = "ar" if str(language).startswith("ar") else "en"
        return _MESSAGES.get(status, _MESSAGES["no_plan"])[lang]
