"""LangGraph agent state (short-term memory).

The state is a ``TypedDict`` so LangGraph can checkpoint it. Per the architecture
rules, all state fields are explicitly typed and there are no dynamic/unknown
keys. Pydantic models are stored as their ``.model_dump()`` dicts where they
cross the graph boundary to keep checkpoint serialization simple and portable.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Message(TypedDict):
    """A single conversation turn stored in short-term memory."""

    role: str  # "user" | "assistant"
    content: str


class TraceEntry(TypedDict, total=False):
    """A lightweight observability record appended during a run."""

    stage: str
    detail: str
    elapsed_ms: float


class AgentState(TypedDict, total=False):
    """The full graph state threaded through every node.

    Fields are populated progressively as the pipeline advances:

      planner            -> plan, language
      entity_resolver    -> resolved_entities
      api_selector       -> selected_apis
      executor           -> execution_results, errors
      relationship_*     -> resolved_results
      context_builder    -> context
      response_validator -> validation
      response_generator -> final_response
      memory_manager     -> (persists; may set saved_memory)
    """

    # --- inputs / conversation ---------------------------------------------
    user_input: str
    messages: Annotated[list[Message], operator.add]
    language: str
    trace_id: str
    thread_id: str

    # --- retrieved long-term memory ----------------------------------------
    retrieved_memories: list[dict[str, Any]]

    # --- planning -----------------------------------------------------------
    plan: dict[str, Any]  # ExecutionPlan.model_dump()

    # --- entity resolution (names -> ids) ----------------------------------
    # step_id -> {"facet": str, "id": Any, "label": str, "record": dict}
    resolved_entities: dict[str, Any]

    # --- clarification (ambiguous entity disambiguation) -------------------
    # {"needed": bool, "facet": str, "query": str, "original_question": str,
    #  "candidates": [{"id": Any, "label": str, "hint": str}]}
    # Persisted across turns so the next turn can resolve the user's choice.
    clarification: dict[str, Any]

    # --- api selection ------------------------------------------------------
    selected_apis: list[dict[str, Any]]

    # --- execution ----------------------------------------------------------
    execution_results: list[dict[str, Any]]  # list[ApiResult.model_dump()]
    # Reset each turn by the planner, then appended to within the turn.
    errors: list[str]

    # --- relationship resolution -------------------------------------------
    resolved_results: list[dict[str, Any]]

    # --- analytics ("SQL mode") --------------------------------------------
    # list[AnalyticsResult.model_dump()] computed from aggregate steps.
    analytics: list[dict[str, Any]]

    # --- context building ---------------------------------------------------
    context: dict[str, Any]

    # --- validation ---------------------------------------------------------
    validation: dict[str, Any]

    # --- response -----------------------------------------------------------
    final_response: str

    # --- observability ------------------------------------------------------
    # Reset each turn by the planner, then appended to within the turn.
    trace: list[TraceEntry]
