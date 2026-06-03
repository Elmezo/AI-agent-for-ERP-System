"""Typed models describing the dynamic API catalog and call results.

These models are the *contract* between configuration files
(``api_registry.json``, ``facets.yaml``) and the rest of the agent. Everything
downstream (adapters, services, nodes) operates on these typed objects rather
than raw dictionaries, so changing the configuration never requires changing
agent logic.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HttpMethod(str, Enum):
    """Supported HTTP verbs for registry endpoints."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ApiEndpoint(BaseModel):
    """A single callable API as declared in ``api_registry.json``.

    Attributes:
        name: Logical name, e.g. ``people.get_by_id``.
        url: Path template relative to ``ERP_BASE_URL``, e.g. ``/api/people/{id}``.
        method: HTTP method.
        path_params: Names of parameters interpolated into ``url``.
        query_params: Names of accepted query-string parameters.
        body_params: Names of accepted JSON body parameters (for write methods).
        facet: The business facet this endpoint belongs to (e.g. ``people``).
        description: Human/LLM-friendly description of what the endpoint returns.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    method: HttpMethod = HttpMethod.GET
    path_params: tuple[str, ...] = Field(default_factory=tuple)
    query_params: tuple[str, ...] = Field(default_factory=tuple)
    body_params: tuple[str, ...] = Field(default_factory=tuple)
    facet: str | None = None
    description: str = ""

    def required_params(self) -> tuple[str, ...]:
        """Return path parameters, which are always required to build the URL."""
        return self.path_params


class RelationshipDef(BaseModel):
    """A foreign-key relationship from one facet's field to another facet.

    Example: ``dataset.createdBy`` -> ``people.id`` is expressed on the
    ``datasets`` facet as ``RelationshipDef(field="createdBy",
    target_facet="people", target_field="id", as_name="createdByName")``.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    target_facet: str
    target_field: str = "id"
    # Name used for the resolved, human-readable value in output.
    as_name: str | None = None

    def resolved_name(self) -> str:
        """Name under which the resolved display value should be stored."""
        if self.as_name:
            return self.as_name
        # createdBy -> createdByName ; ownerId -> ownerName
        base = self.field
        if base.lower().endswith("id"):
            base = base[:-2] if base[-2:].lower() == "id" else base
        return f"{base}Name" if not base.endswith("Name") else base


class FacetDef(BaseModel):
    """A business facet (entity type) and how to interact with it.

    Attributes:
        name: Facet key, e.g. ``people``.
        business_name: Human-friendly label, e.g. ``Employees``.
        primary_key: The field that uniquely identifies a record.
        display_fields: Fields to use when building a readable label.
        search_api: Registry endpoint name used to search this facet.
        get_by_id_api: Registry endpoint name used to fetch one record by id.
        list_api: Registry endpoint name used to list all records.
        relationships: Map of local field name -> relationship definition.
    """

    name: str
    business_name: str
    primary_key: str = "id"
    display_fields: tuple[str, ...] = Field(default_factory=tuple)
    search_api: str | None = None
    get_by_id_api: str | None = None
    list_api: str | None = None
    relationships: dict[str, RelationshipDef] = Field(default_factory=dict)

    def display_label(self, record: dict[str, Any]) -> str:
        """Build a human-readable label from ``display_fields``.

        Falls back to the primary key value if no display fields are present.
        """
        parts = [str(record[f]) for f in self.display_fields if record.get(f) is not None]
        if parts:
            return " ".join(parts)
        pk = record.get(self.primary_key)
        return f"{self.business_name} #{pk}" if pk is not None else self.business_name


class ApiStatus(str, Enum):
    """Outcome classification for an API call.

    ``EMPTY`` is deliberately distinct from ``SUCCESS`` so the agent can tell the
    difference between "the API returned no rows" and "the API returned data".
    """

    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"


class ApiResult(BaseModel):
    """Normalised result of an API call.

    The service layer never returns raw HTTP responses; it always converts them
    into an ``ApiResult`` so downstream nodes can reason about success/empty/error
    uniformly.
    """

    api_name: str
    status: ApiStatus
    data: Any | None = None
    error: str | None = None
    status_code: int | None = None
    elapsed_ms: float | None = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        """True when the call succeeded and returned data."""
        return self.status is ApiStatus.SUCCESS

    @property
    def is_empty(self) -> bool:
        """True when the call succeeded but returned no data."""
        return self.status is ApiStatus.EMPTY

    @property
    def is_error(self) -> bool:
        """True when the call failed."""
        return self.status is ApiStatus.ERROR

    @classmethod
    def success(cls, api_name: str, data: Any, **kwargs: Any) -> ApiResult:
        """Build a success/empty result, classifying empties automatically."""
        empty = data is None or (isinstance(data, (list, dict, str)) and len(data) == 0)
        return cls(
            api_name=api_name,
            status=ApiStatus.EMPTY if empty else ApiStatus.SUCCESS,
            data=data,
            **kwargs,
        )

    @classmethod
    def failure(cls, api_name: str, error: str, **kwargs: Any) -> ApiResult:
        """Build an error result."""
        return cls(api_name=api_name, status=ApiStatus.ERROR, error=error, **kwargs)
