"""Tests for the LLM planner's data-vs-chat routing.

The planner must classify each turn: data questions become executable steps,
while anything else (greetings, general chat) becomes a stepless ``CHAT`` plan
answered conversationally. A config-driven keyword net recovers obvious data
questions the model wrongly marks empty, and a missing LLM degrades gracefully.
"""

from __future__ import annotations

from src.config.registry import Registry
from src.llm.ollama_client import LLMError
from src.models.plan import ExecutionPlan, PlanIntent
from src.planner.llm_planner import LLMPlanner


class _FakeLLM:
    """Stand-in LLM whose ``structured`` returns a queued plan or raises."""

    def __init__(self, plan: ExecutionPlan | None = None, error: Exception | None = None) -> None:
        self._plan = plan
        self._error = error
        self.calls = 0

    async def structured(self, system, user, schema, max_repair=2):  # noqa: ANN001
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._plan, {"completion": 7}



async def test_chat_intent_is_passed_through(registry: Registry) -> None:
    llm = _FakeLLM(ExecutionPlan(goal="greet", steps=[], intent=PlanIntent.CHAT, language="en"))
    plan, _ = await LLMPlanner(registry, llm).plan("hi there")
    assert plan.intent is PlanIntent.CHAT
    assert plan.steps == []



async def test_empty_data_plan_for_non_data_becomes_chat(registry: Registry) -> None:
    """Model returns empty DATA steps for chit-chat -> treated as a chat turn."""
    llm = _FakeLLM(ExecutionPlan(goal="", steps=[], intent=PlanIntent.DATA, language="en"))
    plan, _ = await LLMPlanner(registry, llm).plan("tell me a joke")
    assert plan.intent is PlanIntent.CHAT



async def test_empty_plan_recovered_by_keyword_net(registry: Registry) -> None:
    """If the model misses an obvious data question, the keyword net recovers it."""
    llm = _FakeLLM(ExecutionPlan(goal="", steps=[], intent=PlanIntent.DATA, language="en"))
    plan, _ = await LLMPlanner(registry, llm).plan("How many employees are there?")
    assert plan.intent is PlanIntent.DATA
    assert plan.steps  # the rule-based net produced a list step



async def test_llm_unavailable_non_data_degrades_to_chat(registry: Registry) -> None:
    llm = _FakeLLM(error=LLMError("model down"))
    plan, _ = await LLMPlanner(registry, llm).plan("hello")
    assert plan.intent is PlanIntent.CHAT
    assert plan.used_fallback is False



async def test_llm_unavailable_data_uses_keyword_net(registry: Registry) -> None:
    llm = _FakeLLM(error=LLMError("model down"))
    plan, _ = await LLMPlanner(registry, llm).plan("How many employees are there?")
    assert plan.intent is PlanIntent.DATA
    assert plan.used_fallback is True



async def test_recall_skips_llm(registry: Registry) -> None:
    llm = _FakeLLM(ExecutionPlan(goal="", steps=[], intent=PlanIntent.DATA))
    plan, _ = await LLMPlanner(registry, llm).plan("what was my last question?")
    assert plan.intent is PlanIntent.RECALL
    assert llm.calls == 0  # deterministic path never touches the model


async def test_explanation_followup_routes_to_recall(registry: Registry) -> None:
    """"How did you calculate that?" is a meta turn answered from history.

    Regression guard: it must NOT become a chat turn (which previously triggered
    an unrelated web search and hallucinated business data).
    """
    llm = _FakeLLM(ExecutionPlan(goal="", steps=[], intent=PlanIntent.CHAT))
    plan, _ = await LLMPlanner(registry, llm).plan("حسبتها ازاي")
    assert plan.intent is PlanIntent.RECALL
    assert plan.steps == []
    assert llm.calls == 0
