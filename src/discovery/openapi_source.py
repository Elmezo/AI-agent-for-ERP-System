"""OpenAPI / Swagger discovery source."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from src.discovery.base import ApiSource
from src.models.api import ApiEndpoint, HttpMethod

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class OpenApiSource(ApiSource):
    """Parse an OpenAPI 3.x (or Swagger 2.0) document into endpoints."""

    name = "openapi"

    def load(self) -> list[ApiEndpoint]:
        """Read the spec (JSON or YAML) and emit one endpoint per operation."""
        text = self.path.read_text(encoding="utf-8")
        spec: dict[str, Any] = (
            json.loads(text) if self.path.suffix.lower() == ".json" else yaml.safe_load(text)
        )
        endpoints: list[ApiEndpoint] = []
        for url, path_item in (spec.get("paths") or {}).items():
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                endpoints.append(self._build_endpoint(url, method, operation))
        return endpoints

    def _build_endpoint(self, url: str, method: str, operation: dict[str, Any]) -> ApiEndpoint:
        """Build a single ``ApiEndpoint`` from one OpenAPI operation."""
        operation_id = operation.get("operationId") or self._fallback_name(url, method)

        path_params: list[str] = list(_PATH_PARAM_RE.findall(url))
        query_params: list[str] = []
        body_params: list[str] = []
        for param in operation.get("parameters", []):
            location = param.get("in")
            pname = param.get("name")
            if not pname:
                continue
            if location == "query":
                query_params.append(pname)
            elif location == "path" and pname not in path_params:
                path_params.append(pname)

        facet = operation.get("x-facet")
        if not facet:
            tags = operation.get("tags") or []
            facet = tags[0] if tags else self._facet_from_operation_id(operation_id)

        return ApiEndpoint(
            name=operation_id,
            url=url,
            method=HttpMethod(method.upper()),
            path_params=tuple(path_params),
            query_params=tuple(query_params),
            body_params=tuple(body_params),
            facet=facet,
            description=operation.get("summary") or operation.get("description", ""),
        )

    @staticmethod
    def _fallback_name(url: str, method: str) -> str:
        """Synthesise a stable name when no operationId is present."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")
        return f"{slug}.{method.lower()}"
