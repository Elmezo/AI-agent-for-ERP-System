"""Typed models for the web-search capability.

The service never returns raw provider payloads; it normalises them into these
Pydantic models so downstream code (and tests) depend on a stable shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebResult(BaseModel):
    """A single web search hit, trimmed to what the LLM needs to cite."""

    title: str = ""
    url: str = ""
    content: str = ""
    score: float = 0.0


class WebSearchResult(BaseModel):
    """Normalised result of a web search.

    ``answer`` is the provider's synthesised answer (when requested); ``results``
    are the supporting sources. ``error`` is set when the search failed, so the
    caller can degrade gracefully instead of raising into the graph.
    """

    query: str
    answer: str = ""
    results: list[WebResult] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the search succeeded and produced usable content."""
        return self.error is None and bool(self.answer or self.results)


class WebSearchDecision(BaseModel):
    """The planner-style decision on whether a chat turn needs the web.

    Produced via structured LLM output so weak local models stay reliable
    (no dependence on native tool-calling).
    """

    needs_search: bool = False
    # The optimised search query to run when ``needs_search`` is true.
    query: str = ""
