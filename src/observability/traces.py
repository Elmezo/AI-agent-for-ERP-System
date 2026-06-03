"""Per-request tracing.

A ``Trace`` accumulates structured spans for a single user turn: the question,
the generated plan, the APIs that were called, timings, token usage, and errors.
It is intentionally storage-agnostic - it logs spans and can return a summary
dict that the agent attaches to its state / prints on demand.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.observability.logging import get_logger

_log = get_logger("trace")


@dataclass
class Span:
    """A single timed stage within a trace."""

    name: str
    elapsed_ms: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    """Collects spans and headline metrics for one agent run."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    question: str = ""
    spans: list[Span] = field(default_factory=list)
    api_calls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    plan: dict[str, Any] | None = None

    @contextmanager
    def span(self, name: str, **detail: Any) -> Iterator[Span]:
        """Time a block of work and record it as a span.

        Usage:
            with trace.span("planner"):
                ...
        """
        start = time.perf_counter()
        current = Span(name=name, elapsed_ms=0.0, detail=dict(detail))
        try:
            yield current
        finally:
            current.elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            self.spans.append(current)
            _log.debug("span", trace_id=self.trace_id, stage=name, elapsed_ms=current.elapsed_ms)

    def record_api_call(self, api_name: str) -> None:
        """Record that an API endpoint was invoked."""
        self.api_calls.append(api_name)

    def record_error(self, message: str) -> None:
        """Record an error encountered during the run."""
        self.errors.append(message)

    def add_tokens(self, prompt: int = 0, completion: int = 0) -> None:
        """Accumulate token usage reported by the LLM."""
        self.token_usage["prompt"] = self.token_usage.get("prompt", 0) + prompt
        self.token_usage["completion"] = self.token_usage.get("completion", 0) + completion
        self.token_usage["total"] = self.token_usage.get("total", 0) + prompt + completion

    def summary(self) -> dict[str, Any]:
        """Return a compact dict summarising the run (for logging/state)."""
        return {
            "trace_id": self.trace_id,
            "question": self.question,
            "total_ms": round(sum(s.elapsed_ms for s in self.spans), 2),
            "stages": {s.name: s.elapsed_ms for s in self.spans},
            "api_calls": list(self.api_calls),
            "errors": list(self.errors),
            "token_usage": dict(self.token_usage),
            "used_fallback": bool(self.plan and self.plan.get("used_fallback")),
        }

    def log_summary(self) -> None:
        """Emit the trace summary at INFO level."""
        _log.info("trace_summary", **self.summary())
