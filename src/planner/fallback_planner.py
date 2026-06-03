"""Rule-based fallback planner.

Used when the LLM planner fails, errors, or returns an empty plan. It is fully
deterministic (no model) so the pipeline can always make a best-effort attempt.
It relies only on the registry/facets and a small set of keyword heuristics.
"""

from __future__ import annotations

import re

from src.config.registry import Registry
from src.models.plan import ExecutionPlan, PlanIntent, PlanStep, StepKind
from src.nodes.conversation import detect_recall_topic

# Intent keywords (English + Arabic) used to classify the question.
_COUNT_WORDS = ("how many", "count", "number of", "list all", "all the", "كم", "عدد", "اعرض", "كل")
_LIST_WORDS = ("list", "show all", "كل", "اعرض", "قائمة")

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

        if facet is None:
            return ExecutionPlan(goal=goal, steps=[], language=language, used_fallback=True)

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

    # --- heuristics ---------------------------------------------------------
    def _build_facet_synonyms(self) -> dict[str, list[str]]:
        """Map each facet to its keyword synonyms (from config + built-ins)."""
        builtin: dict[str, list[str]] = {
            "people": ["people", "person", "employee", "employees", "staff", "موظف", "موظفين", "شخص"],
            "org_units": ["org unit", "org-unit", "organizational unit", "department", "departments",
                          "unit", "قسم", "إدارة", "ادارة", "وحدة"],
            "systems": ["system", "systems", "application", "app", "نظام", "أنظمة", "انظمة", "تطبيق"],
            "datasets": ["dataset", "datasets", "data set", "data", "بيانات", "مجموعة بيانات"],
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
