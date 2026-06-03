"""Structured execution-plan models.

The planner never returns free text. It returns an ``ExecutionPlan`` validated
against these models. The same structure is produced by the LLM planner and the
rule-based fallback planner, so downstream nodes are agnostic to which one ran.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepKind(str, Enum):
    """The kind of action a plan step represents."""

    SEARCH = "search"          # resolve a named entity to an id
    GET_BY_ID = "get_by_id"    # fetch one record by primary key
    LIST = "list"              # list all records of a facet
    API = "api"                # call an explicit registry endpoint
    CONCEPT = "concept"        # resolve a business concept (semantic catalog)


class PlanIntent(str, Enum):
    """High-level intent of a turn.

    ``DATA`` questions are answered by executing API steps. ``RECALL`` questions
    are *about the conversation itself* (e.g. "what did I ask before?") and are
    answered from short-term conversation history, not the ERP backend.
    ``SMALLTALK`` turns are social/meta (greetings, thanks, "what can you do?")
    and are answered deterministically without the ERP backend or the LLM.
    """

    DATA = "data"
    RECALL = "recall"
    SMALLTALK = "smalltalk"


class PlanStep(BaseModel):
    """One step of an execution plan.

    Attributes:
        id: Stable 1-based identifier, used for dependency references.
        kind: What this step does.
        facet: Target facet (e.g. ``systems``).
        action: Concrete registry api name or concept name (optional for
            ``search``/``get_by_id``/``list`` which are derived from the facet).
        query: Free-text search term for ``search`` steps.
        params: Explicit parameters for ``api`` steps.
        depends_on: Ids of steps whose output this step needs (e.g. an id from a
            prior search feeds a ``get_by_id``).
        description: Human-readable rationale.
    """

    id: int
    kind: StepKind
    facet: str | None = None
    action: str | None = None
    query: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    description: str = ""


class ExecutionPlan(BaseModel):
    """A validated, ordered plan for answering a user question."""

    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    # Detected language of the user (BCP-47-ish: "ar", "en", ...).
    language: str = "en"
    # What the turn is fundamentally about (data vs. the conversation itself).
    intent: PlanIntent = PlanIntent.DATA
    # For RECALL plans: which aspect of history the user asked for.
    recall_topic: str | None = None
    # For SMALLTALK plans: which social/meta topic was detected.
    smalltalk_topic: str | None = None
    # Marks plans produced by the rule-based fallback (for observability).
    used_fallback: bool = False

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to execute *and* nothing else to answer.

        Only ``DATA`` turns can be "empty": ``RECALL`` and ``SMALLTALK`` turns
        carry their answer in their intent/topic, not in execution steps.
        """
        return len(self.steps) == 0 and self.intent is PlanIntent.DATA
