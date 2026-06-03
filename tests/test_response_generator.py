"""Tests for the Response Generator's deterministic (LLM-down) fallback.

These guard the regression where the agent dumped raw JSON instead of a
human-readable answer when the response LLM was unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.models.plan import PlanIntent
from src.models.web import WebSearchDecision, WebResult, WebSearchResult
from src.nodes.response_generator import ResponseGeneratorNode

_fallback = ResponseGeneratorNode._fallback_answer


def _chat_deps(
    reply: str,
    *,
    web_search: object | None = None,
    decision: WebSearchDecision | None = None,
) -> SimpleNamespace:
    """Deps with a registry (for capability hints) and a chat-capable LLM."""
    registry = SimpleNamespace(
        facets={"people": SimpleNamespace(business_name="People")}
    )
    llm = SimpleNamespace(
        chat=AsyncMock(return_value=(reply, {"completion": 5})),
        structured=AsyncMock(
            return_value=(decision or WebSearchDecision(needs_search=False), {"completion": 2})
        ),
    )
    return SimpleNamespace(registry=registry, llm=llm, web_search=web_search)


def _chat_state(question: str = "hi", language: str = "en") -> dict:
    return {
        "user_input": question,
        "language": language,
        "plan": {"intent": PlanIntent.CHAT.value},
        "validation": {"status": "no_plan", "message": "couldn't map..."},
        "messages": [{"role": "user", "content": question}],
        "context": {},
    }


def _ctx(**over):
    base = {"question": "", "focus": [], "results": []}
    base.update(over)
    return base


def test_focus_value_returns_clean_name() -> None:
    ctx = _ctx(
        question="Who manages the Finance Department?",
        focus=[{"concept": "manager", "field": "managerId", "value": "Youssef Nabil"}],
    )
    assert _fallback(ctx, "en") == "Youssef Nabil"


def test_question_field_match_returns_manager_without_focus() -> None:
    """No focus marker (e.g. fallback plan) still answers from the item field."""
    ctx = _ctx(
        question="Who manages the Finance Department?",
        results=[
            {
                "facet": "org_units",
                "api": "org_units.get_by_id",
                "status": "success",
                "item": {
                    "id": 2,
                    "name": "Finance Department",
                    "code": "FIN",
                    "manager": "Youssef Nabil",
                    "parent": "Executive Office",
                },
            }
        ],
    )
    assert _fallback(ctx, "en") == "Youssef Nabil"


def test_owner_question_matches_owner_field() -> None:
    ctx = _ctx(
        question="Who owns System ABC?",
        results=[{"status": "success", "item": {"name": "System ABC", "owner": "Ahmed Mohamed"}}],
    )
    assert _fallback(ctx, "en") == "Ahmed Mohamed"


def test_count_question() -> None:
    ctx = _ctx(question="How many employees?", results=[{"status": "success", "count": 42}])
    assert _fallback(ctx, "en") == "Count: 42"
    assert _fallback(ctx, "ar") == "العدد: 42"


def test_readable_summary_never_returns_raw_json() -> None:
    """An item with no question-field match yields a readable line, not JSON."""
    ctx = _ctx(
        question="Tell me about the unit",
        results=[{"status": "success", "item": {"id": 2, "name": "Finance Department", "code": "FIN"}}],
    )
    answer = _fallback(ctx, "en")
    assert not answer.lstrip().startswith(("[", "{"))
    assert "Finance Department" in answer


def test_no_data_message() -> None:
    assert _fallback(_ctx(), "en") == "No data available."
    assert _fallback(_ctx(), "ar") == "لا توجد بيانات متاحة."


async def test_chat_intent_answers_conversationally_with_history() -> None:
    """A CHAT turn is answered by the LLM using the conversation history."""
    deps = _chat_deps("Hello! How can I help you today?")
    node = ResponseGeneratorNode(deps)
    state = {
        "user_input": "hi",
        "language": "en",
        "plan": {"intent": PlanIntent.CHAT.value},
        "validation": {"status": "no_plan", "message": "couldn't map..."},
        "messages": [{"role": "user", "content": "hi"}],
        "context": {},
    }
    out = await node(state)

    assert out["final_response"] == "Hello! How can I help you today?"
    # The deterministic "no_plan" message must NOT leak for a chat turn.
    assert "couldn't map" not in out["final_response"]
    # History (incl. the current turn) was forwarded to the LLM.
    deps.llm.chat.assert_awaited_once()
    assert deps.llm.chat.await_args.kwargs["history"] == state["messages"]


async def test_chat_intent_falls_back_when_llm_down() -> None:
    """If the chat LLM raises, a minimal localized greeting is returned."""
    deps = _chat_deps("unused")
    deps.llm.chat = AsyncMock(side_effect=RuntimeError("model down"))
    node = ResponseGeneratorNode(deps)
    state = _chat_state("اهلا", "ar")
    out = await node(state)
    assert out["final_response"] == "مرحباً! كيف يمكنني مساعدتك؟"


async def test_chat_searches_web_when_decision_says_so() -> None:
    """A real-time question triggers a web search whose results ground the reply."""
    search_result = WebSearchResult(
        query="weather cairo today",
        answer="Sunny, ~30C in Cairo.",
        results=[WebResult(title="Cairo Weather", url="https://ex.com/c", content="Sunny 30C")],
    )
    web_search = SimpleNamespace(search=AsyncMock(return_value=search_result))
    deps = _chat_deps(
        "It's sunny and about 30C in Cairo today (source: ex.com).",
        web_search=web_search,
        decision=WebSearchDecision(needs_search=True, query="weather cairo today"),
    )
    node = ResponseGeneratorNode(deps)
    out = await node(_chat_state("What is the weather in Cairo today?"))

    web_search.search.assert_awaited_once_with("weather cairo today")
    # The search findings were injected into the chat system prompt.
    system_prompt = deps.llm.chat.await_args.kwargs["system"]
    assert "Web search results" in system_prompt
    assert "Sunny, ~30C in Cairo." in system_prompt
    assert "30C" in out["final_response"]


async def test_chat_skips_web_search_when_not_needed() -> None:
    """A greeting must not trigger a web search even when search is enabled."""
    web_search = SimpleNamespace(search=AsyncMock())
    deps = _chat_deps(
        "Hello! How can I help?",
        web_search=web_search,
        decision=WebSearchDecision(needs_search=False),
    )
    node = ResponseGeneratorNode(deps)
    await node(_chat_state("hi"))

    web_search.search.assert_not_awaited()
    assert deps.llm.chat.await_args.kwargs["system"].count("Web search results") == 0


async def test_chat_no_decision_call_when_web_search_disabled() -> None:
    """With web search unconfigured, the decision LLM call is skipped entirely."""
    deps = _chat_deps("Hi there!")  # web_search defaults to None
    node = ResponseGeneratorNode(deps)
    await node(_chat_state("what's the latest news?"))
    deps.llm.structured.assert_not_awaited()


async def test_chat_continues_when_web_search_fails() -> None:
    """If the search errors, the assistant still replies (without web context)."""
    web_search = SimpleNamespace(
        search=AsyncMock(return_value=WebSearchResult(query="q", error="boom"))
    )
    deps = _chat_deps(
        "I'm not sure about the very latest, but here's what I know...",
        web_search=web_search,
        decision=WebSearchDecision(needs_search=True, query="q"),
    )
    node = ResponseGeneratorNode(deps)
    out = await node(_chat_state("latest news?"))

    web_search.search.assert_awaited_once()
    assert "Web search results" not in deps.llm.chat.await_args.kwargs["system"]
    assert out["final_response"].startswith("I'm not sure")
