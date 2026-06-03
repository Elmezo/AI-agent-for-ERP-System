"""Schema Understanding models.

The semantic catalog maps *business concepts* (the words users actually say,
like "owner" or "stakeholders") onto the technical means of answering them: a
field that holds a foreign key, or a dedicated API. This lets the agent answer
"who owns the finance system?" even though there is no ``get_owner`` endpoint.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator


class ConceptDef(BaseModel):
    """A single business concept attached to a facet.

    Exactly one of ``field`` or ``api`` should be provided:
      * ``field`` + ``target``: the concept is a foreign key on the facet's
        records that points at another facet (resolve via relationship).
      * ``api``: the concept is answered by calling a dedicated endpoint.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    field: str | None = None
    api: str | None = None
    target: str | None = None
    description: str = ""

    @model_validator(mode="after")
    def _check_one_of(self) -> ConceptDef:
        if not self.field and not self.api:
            raise ValueError(f"concept '{self.name}' must define either 'field' or 'api'")
        return self


class FacetSemantics(BaseModel):
    """Business naming and concepts for a single facet."""

    facet: str
    business_name: str
    concepts: dict[str, ConceptDef] = {}

    def find_concept(self, term: str) -> ConceptDef | None:
        """Case-insensitive lookup of a concept by name."""
        lowered = term.strip().lower()
        for key, concept in self.concepts.items():
            if key.lower() == lowered or concept.name.lower() == lowered:
                return concept
        return None


class SemanticCatalog(BaseModel):
    """The full set of facet semantics, keyed by facet name."""

    facets: dict[str, FacetSemantics] = {}

    def get(self, facet: str) -> FacetSemantics | None:
        """Return semantics for a facet, if present."""
        return self.facets.get(facet)

    def all_concept_terms(self) -> dict[str, list[str]]:
        """Return ``{facet: [concept terms]}`` for prompting / selection."""
        return {name: list(sem.concepts.keys()) for name, sem in self.facets.items()}
