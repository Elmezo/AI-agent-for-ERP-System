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
    AGGREGATE = "aggregate"    # compute analytics over a prior list step's rows


class AggregateOp(str, Enum):
    """Supported analytics operations over a set of records."""

    COUNT = "count"  # number of (filtered) rows; ignores ``metric``
    SUM = "sum"      # total of ``metric`` across rows
    AVG = "avg"      # mean of ``metric``
    MIN = "min"      # smallest ``metric``
    MAX = "max"      # largest ``metric``


class FilterOp(str, Enum):
    """Comparison operators for filtering rows before aggregation."""

    EQ = "eq"            # equal (string-insensitive for text)
    NE = "ne"            # not equal
    GT = "gt"            # greater than (numeric)
    GTE = "gte"          # greater than or equal (numeric)
    LT = "lt"            # less than (numeric)
    LTE = "lte"          # less than or equal (numeric)
    CONTAINS = "contains"  # case-insensitive substring match (text)


class FilterClause(BaseModel):
    """One ``field <op> value`` predicate applied before aggregating."""

    field: str
    op: FilterOp = FilterOp.EQ
    value: Any = None


class AggregateSpec(BaseModel):
    """A declarative analytics request evaluated in memory over list rows.

    Examples:
        * count active projects -> ``op=count, filters=[status eq Active]``
        * average project budget -> ``op=avg, metric=budget``
        * top 5 projects by budget -> ``op=max, metric=budget, limit=5``
        * projects grouped by owner -> ``op=count, group_by=owner``
        * total budget per department -> ``op=sum, metric=budget, group_by=orgUnit``
    """

    op: AggregateOp = AggregateOp.COUNT
    # Numeric field for sum/avg/min/max (and the ranking key for top-N).
    metric: str | None = None
    # Field to group rows by; when set the result is one value per group.
    group_by: str | None = None
    filters: list[FilterClause] = Field(default_factory=list)
    # For ranked/grouped output: sort descending and keep the first ``limit``.
    sort_desc: bool = True
    limit: int | None = None


class PlanIntent(str, Enum):
    """High-level intent of a turn.

    ``DATA`` questions are answered by executing API steps. ``RECALL`` questions
    are *about the conversation itself* (e.g. "what did I ask before?") and are
    answered from short-term conversation history, not the ERP backend.
    ``CHAT`` turns are anything that is not a data lookup (greetings, general
    questions, chit-chat) and are answered conversationally by the LLM using the
    chat history, exactly like a general assistant.
    """

    DATA = "data"
    RECALL = "recall"
    CHAT = "chat"


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
    # Present only for ``aggregate`` steps: the analytics to compute over the
    # rows produced by the list step(s) in ``depends_on`` (or the same facet).
    aggregate: AggregateSpec | None = None


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
    # Marks plans produced by the rule-based fallback (for observability).
    used_fallback: bool = False

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to execute *and* nothing else to answer.

        Only ``DATA`` turns can be "empty": ``RECALL`` and ``CHAT`` turns carry
        their answer in their intent, not in execution steps.
        """
        return len(self.steps) == 0 and self.intent is PlanIntent.DATA
