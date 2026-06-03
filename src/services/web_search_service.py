"""Web search service (Tavily).

A thin, typed, async wrapper around Tavily that the agent uses as a
ChatGPT-style fallback: when the assistant cannot answer a general or real-time
question from its own knowledge or the ERP, it searches the web and answers from
the results.

Design notes:
- The Tavily client is injected so the service is trivially unit-testable
  (no network in tests).
- Every call has a timeout and a bounded retry budget (transient errors only).
- The raw provider payload is never leaked; it is normalised into
  :class:`WebSearchResult`. Failures are returned as a result with ``error`` set
  rather than raised, so the calling node degrades gracefully.
"""

from __future__ import annotations

from typing import Any, Protocol

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.models.web import WebResult, WebSearchResult
from src.observability.logging import get_logger

_log = get_logger("web_search")


class _TavilyLike(Protocol):
    """Minimal async interface we need from a Tavily client (eases testing)."""

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class WebSearchService:
    """Config-driven, resilient web search over the Tavily API."""

    def __init__(
        self,
        client: _TavilyLike,
        *,
        max_results: int = 5,
        search_depth: str = "advanced",
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        self._client = client
        self._max_results = max_results
        self._search_depth = search_depth
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        max_results: int = 5,
        search_depth: str = "advanced",
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
    ) -> "WebSearchService":
        """Build a service backed by a real :class:`AsyncTavilyClient`.

        Imported lazily so the dependency is only required when web search is
        actually enabled.
        """
        from tavily import AsyncTavilyClient

        return cls(
            AsyncTavilyClient(api_key),
            max_results=max_results,
            search_depth=search_depth,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    async def search(self, query: str) -> WebSearchResult:
        """Search the web for ``query`` and return a normalised result.

        Never raises: transport/provider failures are captured in
        :attr:`WebSearchResult.error`.
        """
        query = (query or "").strip()
        if not query:
            return WebSearchResult(query="", error="empty query")

        try:
            payload = await self._search_with_retries(query)
        except Exception as exc:  # exhausted retries / fatal provider error
            _log.warning("web_search_failed", query=query[:120], error=str(exc))
            return WebSearchResult(query=query, error=str(exc))

        result = self._normalise(query, payload)
        _log.info("web_searched", query=query[:120], results=len(result.results))
        return result

    async def _search_with_retries(self, query: str) -> dict[str, Any]:
        """Call Tavily with exponential backoff on transient errors."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(multiplier=0.5, max=8),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                return await self._client.search(
                    query,
                    search_depth=self._search_depth,
                    max_results=self._max_results,
                    include_answer=True,
                    timeout=self._timeout,
                )
        raise RuntimeError("unreachable")  # pragma: no cover

    @staticmethod
    def _normalise(query: str, payload: dict[str, Any]) -> WebSearchResult:
        """Turn a raw Tavily payload into a typed :class:`WebSearchResult`."""
        raw_results = payload.get("results") or []
        results = [
            WebResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                content=str(item.get("content", "")),
                score=float(item.get("score", 0.0) or 0.0),
            )
            for item in raw_results
            if isinstance(item, dict)
        ]
        return WebSearchResult(
            query=query,
            answer=str(payload.get("answer") or ""),
            results=results,
        )
