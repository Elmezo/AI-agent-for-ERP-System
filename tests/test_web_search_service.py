"""Tests for the Tavily-backed web search service.

The Tavily client is faked so tests never hit the network. They cover the happy
path, payload normalisation, transient-error retries, and graceful failure.
"""

from __future__ import annotations

from typing import Any

from src.services.web_search_service import WebSearchService

_PAYLOAD: dict[str, Any] = {
    "query": "weather in cairo today",
    "answer": "It is sunny and around 30C in Cairo today.",
    "results": [
        {
            "title": "Cairo Weather",
            "url": "https://example.com/cairo",
            "content": "Sunny, 30C, light wind.",
            "score": 0.91,
        },
        {"title": "Forecast", "url": "https://example.com/fc", "content": "Clear skies."},
    ],
}


class _FakeTavily:
    """Async Tavily stand-in that returns a payload or fails a few times first."""

    def __init__(self, payload: dict[str, Any] | None = None, fail_times: int = 0) -> None:
        self._payload = payload if payload is not None else _PAYLOAD
        self._fail_times = fail_times
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self._fail_times:
            raise ConnectionError("transient")
        return self._payload


async def test_search_normalises_payload() -> None:
    client = _FakeTavily()
    service = WebSearchService(client, max_results=5, search_depth="advanced")
    result = await service.search("weather in cairo today")

    assert result.ok
    assert result.error is None
    assert result.answer.startswith("It is sunny")
    assert len(result.results) == 2
    assert result.results[0].url == "https://example.com/cairo"
    assert result.results[0].score == 0.91
    # Request options were forwarded to the client.
    assert client.last_kwargs["include_answer"] is True
    assert client.last_kwargs["max_results"] == 5
    assert client.last_kwargs["search_depth"] == "advanced"


async def test_empty_query_short_circuits() -> None:
    client = _FakeTavily()
    service = WebSearchService(client)
    result = await service.search("   ")
    assert result.error == "empty query"
    assert client.calls == 0


async def test_retries_then_succeeds() -> None:
    client = _FakeTavily(fail_times=1)
    service = WebSearchService(client, max_retries=2)
    result = await service.search("anything")
    assert result.ok
    assert client.calls == 2  # one failure + one success


async def test_failure_returns_error_result_not_raise() -> None:
    client = _FakeTavily(fail_times=99)  # always fails
    service = WebSearchService(client, max_retries=1)
    result = await service.search("anything")
    assert not result.ok
    assert result.error is not None
    assert client.calls == 2  # initial + one retry
