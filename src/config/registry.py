"""Load and validate the dynamic configuration into typed objects.

This module turns the three config artifacts into a single in-memory ``Registry``:

  * ``config/api_registry.json``  -> ``ApiEndpoint`` objects
  * ``config/facets.yaml``        -> ``FacetDef`` objects (incl. relationships)
  * ``schema/semantic_catalog.yaml`` -> ``SemanticCatalog``

The ``Registry`` is the single source of truth consumed by the service layer,
tool factory, planner, and resolver nodes. It is built once and injected.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.config.settings import Settings
from src.models.api import ApiEndpoint, FacetDef, HttpMethod, RelationshipDef
from src.models.semantic import ConceptDef, FacetSemantics, SemanticCatalog


def load_endpoints(path: Path) -> dict[str, ApiEndpoint]:
    """Parse ``api_registry.json`` into a name -> ``ApiEndpoint`` map."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    endpoints: dict[str, ApiEndpoint] = {}
    for name, spec in raw.items():
        endpoints[name] = ApiEndpoint(
            name=name,
            url=spec["url"],
            method=HttpMethod(spec.get("method", "GET").upper()),
            path_params=tuple(spec.get("path_params", [])),
            query_params=tuple(spec.get("query_params", [])),
            body_params=tuple(spec.get("body_params", [])),
            facet=spec.get("facet"),
            description=spec.get("description", ""),
        )
    return endpoints


def load_facets(path: Path) -> dict[str, FacetDef]:
    """Parse ``facets.yaml`` into a name -> ``FacetDef`` map."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    facets: dict[str, FacetDef] = {}
    for name, spec in raw.items():
        relationships: dict[str, RelationshipDef] = {}
        for field, rel in (spec.get("relationships") or {}).items():
            # rel may be "people.id" shorthand or a mapping.
            if isinstance(rel, str):
                target_facet, _, target_field = rel.partition(".")
                relationships[field] = RelationshipDef(
                    field=field,
                    target_facet=target_facet,
                    target_field=target_field or "id",
                )
            else:
                relationships[field] = RelationshipDef(
                    field=field,
                    target_facet=rel["target"],
                    target_field=rel.get("target_field", "id"),
                    as_name=rel.get("as_name"),
                )
        facets[name] = FacetDef(
            name=name,
            business_name=spec.get("business_name", name.title()),
            primary_key=spec.get("primary_key", "id"),
            display_fields=tuple(spec.get("display_fields", [])),
            search_api=spec.get("search_api"),
            get_by_id_api=spec.get("get_by_id_api"),
            list_api=spec.get("list_api"),
            relationships=relationships,
        )
    return facets


def load_semantic_catalog(path: Path) -> SemanticCatalog:
    """Parse ``semantic_catalog.yaml`` into a ``SemanticCatalog``.

    Missing file is tolerated (returns an empty catalog) so the agent still runs
    on a registry that has no semantic layer yet.
    """
    p = Path(path)
    if not p.exists():
        return SemanticCatalog()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    facets: dict[str, FacetSemantics] = {}
    for facet_name, spec in raw.items():
        concepts: dict[str, ConceptDef] = {}
        for cname, cspec in (spec.get("concepts") or {}).items():
            concepts[cname] = ConceptDef(
                name=cname,
                field=cspec.get("field"),
                api=cspec.get("api"),
                target=cspec.get("target"),
                reverse_facet=cspec.get("reverse_facet"),
                reverse_field=cspec.get("reverse_field"),
                description=cspec.get("description", ""),
            )
        facets[facet_name] = FacetSemantics(
            facet=facet_name,
            business_name=spec.get("business_name", facet_name.title()),
            concepts=concepts,
        )
    return SemanticCatalog(facets=facets)


class Registry:
    """In-memory, validated view of the dynamic API configuration."""

    def __init__(
        self,
        endpoints: dict[str, ApiEndpoint],
        facets: dict[str, FacetDef],
        semantic: SemanticCatalog,
    ) -> None:
        self._endpoints = endpoints
        self._facets = facets
        self._semantic = semantic

    # --- construction -------------------------------------------------------
    @classmethod
    def from_settings(cls, settings: Settings) -> Registry:
        """Build a registry from the paths declared in settings."""
        return cls(
            endpoints=load_endpoints(settings.api_registry_path),
            facets=load_facets(settings.facets_path),
            semantic=load_semantic_catalog(settings.semantic_catalog_path),
        )

    # --- endpoints ----------------------------------------------------------
    @property
    def endpoints(self) -> dict[str, ApiEndpoint]:
        """All endpoints keyed by name."""
        return self._endpoints

    def get_endpoint(self, name: str) -> ApiEndpoint | None:
        """Return an endpoint by name, or ``None``."""
        return self._endpoints.get(name)

    def require_endpoint(self, name: str) -> ApiEndpoint:
        """Return an endpoint by name or raise ``KeyError``."""
        ep = self._endpoints.get(name)
        if ep is None:
            raise KeyError(f"Unknown API endpoint: {name!r}")
        return ep

    def endpoints_for_facet(self, facet: str) -> list[ApiEndpoint]:
        """All endpoints declared against a facet."""
        return [e for e in self._endpoints.values() if e.facet == facet]

    # --- facets -------------------------------------------------------------
    @property
    def facets(self) -> dict[str, FacetDef]:
        """All facets keyed by name."""
        return self._facets

    def get_facet(self, name: str) -> FacetDef | None:
        """Return a facet definition by name, or ``None``."""
        return self._facets.get(name)

    def require_facet(self, name: str) -> FacetDef:
        """Return a facet definition by name or raise ``KeyError``."""
        facet = self._facets.get(name)
        if facet is None:
            raise KeyError(f"Unknown facet: {name!r}")
        return facet

    # --- semantic catalog ---------------------------------------------------
    @property
    def semantic(self) -> SemanticCatalog:
        """The business-concept catalog."""
        return self._semantic

    # --- prompting helpers --------------------------------------------------
    def catalog_summary(self) -> str:
        """A compact, LLM-friendly summary of facets and their key APIs."""
        lines: list[str] = []
        for name, facet in self._facets.items():
            apis = []
            if facet.search_api:
                apis.append(f"search={facet.search_api}")
            if facet.get_by_id_api:
                apis.append(f"get_by_id={facet.get_by_id_api}")
            if facet.list_api:
                apis.append(f"list={facet.list_api}")
            rels = ", ".join(
                f"{f}->{r.target_facet}" for f, r in facet.relationships.items()
            )
            line = f"- {name} ({facet.business_name}): {', '.join(apis) or 'no standard apis'}"
            if rels:
                line += f"; relationships: {rels}"
            lines.append(line)
        return "\n".join(lines)
