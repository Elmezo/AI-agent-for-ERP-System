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

    Exactly one of these three resolution strategies must be provided:
      * ``field`` + ``target``: a *forward* foreign key on the facet's records
        that points at another facet (resolve via relationship), e.g.
        ``systems.owner -> people`` via ``ownerId``.
      * ``api``: the concept is answered by calling a dedicated endpoint.
      * ``reverse_facet`` + ``reverse_field``: a *reverse* relationship — the
        records of ``reverse_facet`` that point *back* at this facet through
        ``reverse_field``, e.g. ``org_units.members`` = the ``people`` whose
        ``orgUnitId`` equals this org unit's id. Reverse concepts are answered by
        a child-of-parent join, not a single field lookup.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    field: str | None = None
    api: str | None = None
    target: str | None = None
    # Reverse (child-of-parent) relationship: the facet that points back here…
    reverse_facet: str | None = None
    # …through this foreign-key field on that facet (matched to our primary key).
    reverse_field: str | None = None
    description: str = ""

    @property
    def is_reverse(self) -> bool:
        """True when this concept resolves a reverse (child-of-parent) link."""
        return bool(self.reverse_facet and self.reverse_field)

    @model_validator(mode="after")
    def _check_one_of(self) -> ConceptDef:
        if not (self.field or self.api or self.is_reverse):
            raise ValueError(
                f"concept '{self.name}' must define 'field', 'api', "
                "or both 'reverse_facet' and 'reverse_field'"
            )
        if bool(self.reverse_facet) != bool(self.reverse_field):
            raise ValueError(
                f"concept '{self.name}' must set 'reverse_facet' and "
                "'reverse_field' together"
            )
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

    def reverse_concept(self, reverse_facet: str) -> ConceptDef | None:
        """Return the reverse concept linking back from ``reverse_facet``, if any.

        E.g. on ``org_units`` this returns the ``members`` concept when asked for
        ``reverse_facet="people"``, exposing the ``orgUnitId`` join key.
        """
        for concept in self.concepts.values():
            if concept.is_reverse and concept.reverse_facet == reverse_facet:
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
