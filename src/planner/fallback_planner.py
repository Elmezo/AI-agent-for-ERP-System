"""Rule-based fallback planner.

Used when the LLM planner fails, errors, or returns an empty plan. It is fully
deterministic (no model) so the pipeline can always make a best-effort attempt.
It relies only on the registry/facets and a small set of keyword heuristics.
"""

from __future__ import annotations

import re

from src.config.registry import Registry
from src.models.plan import (
    AggregateOp,
    AggregateSpec,
    ExecutionPlan,
    FilterClause,
    FilterOp,
    PlanIntent,
    PlanStep,
    StepKind,
)
from src.nodes.conversation import detect_recall_topic

# Intent keywords (English + Arabic) used to classify the question.
_COUNT_WORDS = ("how many", "count", "number of", "list all", "all the", "كم", "عدد", "اعرض", "كل")
_LIST_WORDS = ("list", "show all", "كل", "اعرض", "قائمة")

# --- analytics keyword maps (degraded, LLM-free aggregation detection) ------
_AGG_AVG = ("average", "avg", "mean", "متوسط")
_AGG_SUM = ("total", "sum", "combined", "إجمالي", "اجمالي", "مجموع")
_AGG_MIN = ("minimum", "lowest", "smallest", "cheapest", "أدنى", "ادنى", "أصغر", "اصغر", "أقل", "اقل")
_AGG_MAX = ("maximum", "highest", "largest", "biggest", "most expensive", "أعلى", "اعلى", "أكبر", "اكبر")
_TOP_WORDS = ("top", "highest", "largest", "biggest", "most", "أعلى", "اعلى", "أكبر", "اكبر")
_BOTTOM_WORDS = ("bottom", "lowest", "smallest", "أدنى", "ادنى", "أصغر", "اصغر")
_GROUP_WORDS = ("grouped by", "group by", "per ", "by each", "for each", "حسب", "لكل")

# Word -> numeric metric field name.
_METRIC_SYNONYMS: dict[str, str] = {
    "budget": "budget", "budgets": "budget", "cost": "budget", "costs": "budget",
    "ميزانية": "budget", "الميزانية": "budget", "تكلفة": "budget",
    "spent": "spent", "spend": "spent", "spending": "spent", "المصروف": "spent", "صرف": "spent",
    "allocation": "allocation", "allocated": "allocation", "utilization": "allocation",
    "تخصيص": "allocation", "التخصيص": "allocation",
}
# Word -> group/filter field name (prefers resolved relationship names).
_FIELD_SYNONYMS: dict[str, str] = {
    "owner": "owner", "owners": "owner", "مالك": "owner", "المالك": "owner",
    "department": "orgUnit", "departments": "orgUnit", "dept": "orgUnit",
    "unit": "orgUnit", "org": "orgUnit", "قسم": "orgUnit", "القسم": "orgUnit", "إدارة": "orgUnit",
    "status": "status", "state": "status", "حالة": "status", "الحالة": "status",
    "manager": "manager", "مدير": "manager",
    "creator": "createdByName", "author": "createdByName",
}
# A detected numeric metric strongly implies its owning facet, which helps the
# rule-based planner pick the right list when the facet word is absent or
# ambiguous (e.g. "total budget per department" -> projects, not org_units).
_METRIC_FACET: dict[str, str] = {
    "budget": "projects",
    "spent": "projects",
    "allocation": "people",
}

# Status filter values recognised in free text.
_STATUS_VALUES: dict[str, str] = {
    "active": "Active", "نشط": "Active", "نشطة": "Active",
    "completed": "Completed", "done": "Completed", "مكتمل": "Completed", "مكتملة": "Completed",
    "planned": "Planned", "مخطط": "Planned",
    "on hold": "On Hold", "onhold": "On Hold", "متوقف": "On Hold",
}

# Concept trigger words -> concept name to resolve on the target facet.
_CONCEPT_WORDS: dict[str, tuple[str, ...]] = {
    "owner": ("owner", "owns", "own", "responsible", "صاحب", "مالك", "مسؤول", "مسئول"),
    "manager": ("manager", "manage", "manages", "managed", "managing", "managed by",
                "head of", "led by", "leads", "lead", "مدير", "يدير", "يدِير", "رئيس"),
    "creator": ("creator", "created", "create", "made by", "author", "أنشأ", "انشأ", "منشئ", "من عمل"),
    "stakeholders": ("stakeholder", "stakeholders", "أصحاب المصلحة"),
    "interfaces": ("interface", "interfaces", "connected", "واجهات", "متصل"),
}


def detect_language(text: str) -> str:
    """Very small heuristic: Arabic if it contains Arabic letters, else English."""
    return "ar" if re.search(r"[\u0600-\u06FF]", text or "") else "en"


class FallbackPlanner:
    """Heuristic planner that never relies on the LLM."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._synonyms = self._build_facet_synonyms()

    def plan(self, question: str) -> ExecutionPlan:
        """Produce a best-effort execution plan from keywords."""
        language = detect_language(question)
        lowered = (question or "").lower()

        # Meta/conversational questions are answered from history, not the ERP.
        recall_topic = detect_recall_topic(question)
        if recall_topic is not None:
            return ExecutionPlan(
                goal=question.strip(),
                steps=[],
                language=language,
                intent=PlanIntent.RECALL,
                recall_topic=recall_topic,
                used_fallback=True,
            )

        facet = self._detect_facet(lowered)
        goal = question.strip()

        # Analytics ("SQL mode"): list the facet, then aggregate over the rows.
        # A detected metric can imply (or correct) the facet for analytic turns.
        spec = self._detect_aggregate(question, lowered)
        if spec is not None and spec.metric in _METRIC_FACET:
            facet = _METRIC_FACET[spec.metric]

        if facet is None:
            return ExecutionPlan(goal=goal, steps=[], language=language, used_fallback=True)

        if spec is not None:
            steps = [
                PlanStep(id=1, kind=StepKind.LIST, facet=facet, description=f"List all {facet}"),
                PlanStep(id=2, kind=StepKind.AGGREGATE, facet=facet, depends_on=[1],
                         aggregate=spec, description=f"Aggregate ({spec.op.value}) over {facet}"),
            ]
            return ExecutionPlan(goal=goal, steps=steps, language=language, used_fallback=True)

        concept = self._detect_concept(lowered, facet)
        query = self._extract_query(question, facet)
        is_count = any(w in lowered for w in _COUNT_WORDS)

        steps: list[PlanStep] = []

        # "how many / list all" with no specific entity -> just list the facet.
        if is_count and not query and concept is None:
            steps.append(
                PlanStep(id=1, kind=StepKind.LIST, facet=facet, description=f"List all {facet}")
            )
            return ExecutionPlan(goal=goal, steps=steps, language=language, used_fallback=True)

        if query:
            steps.append(
                PlanStep(id=1, kind=StepKind.SEARCH, facet=facet, query=query,
                         description=f"Find {facet} matching '{query}'")
            )
            steps.append(
                PlanStep(id=2, kind=StepKind.GET_BY_ID, facet=facet, depends_on=[1],
                         description=f"Fetch full {facet} details")
            )
            if concept is not None:
                steps.append(
                    PlanStep(id=3, kind=StepKind.CONCEPT, facet=facet, action=concept,
                             depends_on=[2], description=f"Resolve concept '{concept}'")
                )
            return ExecutionPlan(goal=goal, steps=steps, language=language, used_fallback=True)

        # No entity name extracted: fall back to listing the facet.
        steps.append(
            PlanStep(id=1, kind=StepKind.LIST, facet=facet, description=f"List all {facet}")
        )
        return ExecutionPlan(goal=goal, steps=steps, language=language, used_fallback=True)

    # --- analytics heuristics ----------------------------------------------
    def _detect_aggregate(self, question: str, lowered: str) -> AggregateSpec | None:
        """Build an :class:`AggregateSpec` from keywords, or ``None``.

        Best-effort only (used when the LLM is unavailable). If a guessed field
        does not exist, the analytics service reports it truthfully rather than
        returning a wrong number.
        """
        tokens = re.findall(r"[a-z\u0600-\u06ff]+", lowered)
        token_set = set(tokens)

        op = self._detect_op(lowered)
        metric = self._first_match(token_set, _METRIC_SYNONYMS)
        group_by = self._detect_group_by(lowered, tokens)
        filters = self._detect_filters(question, lowered, token_set)
        top_n = self._detect_top_n(lowered)

        # Only treat as analytics when there's a real aggregation signal.
        is_analytic = bool(
            op or group_by or top_n or filters
            or any(w in lowered for w in _COUNT_WORDS) and (metric or group_by or filters)
        )
        if not is_analytic:
            return None

        sort_desc = not any(w in lowered for w in _BOTTOM_WORDS)
        limit = top_n

        # Resolve the operation: ranking needs a metric; otherwise default count.
        if op is None:
            op = AggregateOp.MAX if (top_n and metric) else AggregateOp.COUNT
        # A bare "count/how many" with no metric/group/filter is just a list count;
        # let the normal LIST path handle it (avoids spurious aggregate steps).
        if op is AggregateOp.COUNT and not group_by and not filters and not top_n:
            return None

        return AggregateSpec(
            op=op,
            metric=metric if op in {AggregateOp.SUM, AggregateOp.AVG, AggregateOp.MIN,
                                    AggregateOp.MAX} or (top_n and metric) else None,
            group_by=group_by,
            filters=filters,
            sort_desc=sort_desc,
            limit=limit,
        )

    @staticmethod
    def _detect_op(lowered: str) -> AggregateOp | None:
        """Map aggregation keywords to an operation."""
        if any(w in lowered for w in _AGG_AVG):
            return AggregateOp.AVG
        if any(w in lowered for w in _AGG_SUM):
            return AggregateOp.SUM
        if any(w in lowered for w in _AGG_MAX):
            return AggregateOp.MAX
        if any(w in lowered for w in _AGG_MIN):
            return AggregateOp.MIN
        return None

    @staticmethod
    def _detect_top_n(lowered: str) -> int | None:
        """Extract N for 'top N' / 'top' (defaults to 10) or bottom N."""
        match = re.search(r"(?:top|bottom|أعلى|اعلى|أكبر|اكبر|أدنى|ادنى)\s+(\d+)", lowered)
        if match:
            return int(match.group(1))
        if any(w in lowered for w in (*_TOP_WORDS, *_BOTTOM_WORDS)):
            return 10
        return None

    @staticmethod
    def _first_match(tokens: set[str], mapping: dict[str, str]) -> str | None:
        """Return the first mapped field whose trigger word appears in tokens."""
        for word, field in mapping.items():
            if word in tokens:
                return field
        return None

    def _detect_group_by(self, lowered: str, tokens: list[str]) -> str | None:
        """Detect an explicit grouping field ('grouped by / per / حسب X')."""
        if not any(g in lowered for g in _GROUP_WORDS):
            return None
        # Look at the word following an explicit grouping cue.
        cues = {"by", "per", "each", "حسب", "لكل"}
        for i, tok in enumerate(tokens):
            if tok in cues and i + 1 < len(tokens):
                field = _FIELD_SYNONYMS.get(tokens[i + 1])
                if field:
                    return field
        # Otherwise, any recognised grouping field mentioned anywhere.
        return self._first_match(set(tokens), _FIELD_SYNONYMS)

    @staticmethod
    def _detect_filters(question: str, lowered: str, tokens: set[str]) -> list[FilterClause]:
        """Detect simple status and 'belong to <Org>' filters."""
        filters: list[FilterClause] = []

        for word, value in _STATUS_VALUES.items():
            if word in lowered:
                filters.append(FilterClause(field="status", op=FilterOp.EQ, value=value))
                break

        # "belong(s) to / in / within <Name>" -> orgUnit contains Name.
        match = re.search(
            r"(?:belong[s]?\s+to|belonging\s+to|in|within|under|for|تابع\s+لـ?|في|ضمن)\s+"
            r"([A-Z\u0600-\u06ff][\w\u0600-\u06ff-]*(?:\s+[A-Z\u0600-\u06ff][\w\u0600-\u06ff-]*)*)",
            question,
        )
        if match:
            name = match.group(1).strip()
            # Drop a trailing facet word if captured (e.g. "Finance Department").
            if name and name.lower() not in ("the", "a", "an"):
                filters.append(FilterClause(field="orgUnit", op=FilterOp.CONTAINS, value=name))
        return filters

    # --- heuristics ---------------------------------------------------------
    def _build_facet_synonyms(self) -> dict[str, list[str]]:
        """Map each facet to its keyword synonyms (from config + built-ins)."""
        builtin: dict[str, list[str]] = {
            "people": ["people", "person", "employee", "employees", "staff", "موظف", "موظفين", "شخص"],
            "org_units": ["org unit", "org-unit", "organizational unit", "department", "departments",
                          "unit", "قسم", "إدارة", "ادارة", "وحدة"],
            "systems": ["system", "systems", "application", "app", "نظام", "أنظمة", "انظمة", "تطبيق"],
            "datasets": ["dataset", "datasets", "data set", "data", "بيانات", "مجموعة بيانات"],
            "projects": ["project", "projects", "initiative", "initiatives",
                         "مشروع", "مشاريع", "المشاريع"],
        }
        synonyms: dict[str, list[str]] = {}
        for name, facet in self._registry.facets.items():
            words = set(builtin.get(name, []))
            words.add(name.replace("_", " "))
            words.add(facet.business_name.lower())
            synonyms[name] = sorted(words)
        return synonyms

    def _detect_facet(self, lowered: str) -> str | None:
        """Return the first facet whose synonyms appear in the question."""
        best: tuple[int, str] | None = None
        for facet, words in self._synonyms.items():
            for word in words:
                idx = lowered.find(word)
                if idx != -1 and (best is None or idx < best[0]):
                    best = (idx, facet)
        return best[1] if best else None

    def _detect_concept(self, lowered: str, facet: str) -> str | None:
        """Return a concept name if its trigger words appear and it exists."""
        semantics = self._registry.semantic.get(facet)
        if semantics is None:
            return None
        for concept_name, triggers in _CONCEPT_WORDS.items():
            if concept_name in semantics.concepts and any(t in lowered for t in triggers):
                return concept_name
        # Also match any concept declared in the catalog by its own name.
        for concept_name in semantics.concepts:
            if concept_name.lower() in lowered:
                return concept_name
        return None

    def _extract_query(self, question: str, facet: str) -> str | None:
        """Extract a likely entity name (quoted text, or trailing capitalised words)."""
        # 1) Quoted text wins.
        quoted = re.search(r"[\"'«](.+?)[\"'»]", question)
        if quoted:
            return quoted.group(1).strip()

        # 2) Sequence of capitalised words / tokens after facet synonyms.
        #    e.g. "owner of System ABC" -> "System ABC".
        caps = re.findall(r"\b([A-Z][\w-]*(?:\s+[A-Z0-9][\w-]*)*)\b", question)
        # Drop leading/standalone question and stop words that may be capitalised.
        stop = {
            "who", "what", "which", "where", "when", "how", "why", "the", "of",
            "is", "are", "there", "many", "much", "show", "me", "list", "all",
            "give", "tell", "find", "get",
        }
        cleaned: list[str] = []
        for phrase in caps:
            tokens = [t for t in phrase.split() if t.lower() not in stop]
            if tokens:
                cleaned.append(" ".join(tokens))
        if cleaned:
            return max(cleaned, key=len).strip()
        return None
