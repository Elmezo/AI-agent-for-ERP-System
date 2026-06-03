"""End-to-end graph test for the clarification branch and multi-turn resume.

Compiles the *real* pipeline graph with an in-memory checkpointer, a stubbed
planner (no LLM), and an in-memory ERP (httpx MockTransport). This exercises the
conditional edge (ambiguous -> ask) and the cross-turn resume (the next turn's
reply resolves the choice from checkpointed state).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from langgraph.checkpoint.memory import MemorySaver

from src.config.settings import Settings
from src.graph.builder import build_deps, build_graph
from src.memory.repository import MemoryRecord
from src.models.plan import ExecutionPlan, PlanStep, StepKind

_AHMEDS = [
    {"id": 1, "name": "Ahmed Mohamed", "title": "CTO", "orgUnitId": 1},
    {"id": 8, "name": "Ahmed Ali", "title": "Sales Manager", "orgUnitId": 2},
    {"id": 9, "name": "Ahmed Hassan", "title": "IT Support", "orgUnitId": 3},
]


class _StubPlanner:
    """Deterministic planner: 'Show <name>'s projects' -> search people <name>."""

    async def plan(self, question: str) -> tuple[ExecutionPlan, dict[str, int]]:
        match = re.search(r"Show (.+?)'s projects", question)
        steps: list[PlanStep] = []
        if match:
            steps = [PlanStep(id=1, kind=StepKind.SEARCH, facet="people", query=match.group(1))]
        plan = ExecutionPlan(goal=question, steps=steps, language="en")
        return plan, {}


class _FakeMemory:
    """No-op long-term memory so the graph runs without a database."""

    async def initialize(self) -> None: ...
    async def add(self, memory: MemoryRecord) -> MemoryRecord:
        return memory
    async def search(self, thread_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        return []
    async def recent(self, thread_id: str, limit: int = 5) -> list[MemoryRecord]:
        return []
    async def close(self) -> None: ...


def _erp_handler(request: httpx.Request) -> httpx.Response:
    """Serve a people search, filtering the canned list by the ``q`` term."""
    if "people" in request.url.path and "search" in request.url.path:
        term = request.url.params.get("q", "").lower()
        matches = [p for p in _AHMEDS if term in p["name"].lower()]
        return httpx.Response(200, json=matches)
    return httpx.Response(200, json=[])


def _build_app() -> Any:
    settings = Settings(
        erp_username=None, erp_password=None,
        erp_base_url="http://erp.test", erp_max_retries=1,
        web_search_enabled=False, tavily_api_key=None,
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_erp_handler), base_url="http://erp.test"
    )
    deps = build_deps(settings, http_client=client, memory=_FakeMemory())
    deps.planner = _StubPlanner()  # type: ignore[assignment]
    return build_graph(deps).compile(checkpointer=MemorySaver())


async def test_ambiguous_turn_asks_then_resolves_next_turn() -> None:
    app = _build_app()
    config = {"configurable": {"thread_id": "clar-e2e"}}

    # Turn 1: ambiguous -> the agent asks instead of guessing.
    turn1 = await app.ainvoke({"user_input": "Show Ahmed's projects", "thread_id": "clar-e2e"}, config)
    answer1 = turn1["final_response"]
    assert "Which one do you mean?" in answer1
    assert "1. Ahmed Mohamed" in answer1
    assert "2. Ahmed Ali" in answer1
    assert "3. Ahmed Hassan" in answer1
    assert turn1["clarification"]["needed"] is True

    # Turn 2: the user picks option 2 -> resume resolves to Ahmed Ali.
    turn2 = await app.ainvoke({"user_input": "2", "thread_id": "clar-e2e"}, config)
    assert "Which one do you mean?" not in turn2["final_response"]
    assert turn2["clarification"]["needed"] is False
    # The pending question was rewritten with the chosen entity.
    assert turn2["user_input"] == "Show Ahmed Ali's projects"
