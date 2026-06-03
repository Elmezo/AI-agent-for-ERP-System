"""Postman Collection (v2.1) discovery source."""

from __future__ import annotations

import json
import re
from typing import Any

from src.discovery.base import ApiSource
from src.models.api import ApiEndpoint, HttpMethod


class PostmanSource(ApiSource):
    """Parse a Postman collection export into endpoints.

    Path segments written as ``:id`` (Postman path variables) are treated as
    path parameters; ``{{var}}`` host placeholders are stripped.
    """

    name = "postman"

    def load(self) -> list[ApiEndpoint]:
        """Read the collection and flatten its (possibly nested) items."""
        spec: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        endpoints: list[ApiEndpoint] = []
        self._walk(spec.get("item", []), endpoints)
        return endpoints

    def _walk(self, items: list[dict[str, Any]], out: list[ApiEndpoint]) -> None:
        """Recursively traverse folders and requests."""
        for item in items:
            if "item" in item:  # folder
                self._walk(item["item"], out)
            elif "request" in item:
                endpoint = self._build_endpoint(item)
                if endpoint is not None:
                    out.append(endpoint)

    def _build_endpoint(self, item: dict[str, Any]) -> ApiEndpoint | None:
        """Build an ``ApiEndpoint`` from a Postman request item."""
        request = item["request"]
        method = (request.get("method") or "GET").upper()
        url = request.get("url")
        if isinstance(url, str):
            path = self._path_from_raw(url)
            query_params: list[str] = []
        else:
            segments = url.get("path", [])
            path = "/" + "/".join(self._normalise_segment(s) for s in segments)
            query_params = [q["key"] for q in (url.get("query") or []) if q.get("key")]

        # After normalisation, path variables are in ``{name}`` template form.
        path_params = re.findall(r"\{([^}]+)\}", path)

        name = item.get("name") or self._fallback_name(path, method)
        return ApiEndpoint(
            name=name,
            url=path,
            method=HttpMethod(method),
            path_params=tuple(path_params),
            query_params=tuple(query_params),
            facet=self._facet_from_operation_id(name),
            description=request.get("description", "") if isinstance(request, dict) else "",
        )

    @staticmethod
    def _normalise_segment(segment: str) -> str:
        """Convert Postman ``:id`` path variables into ``{id}`` template form."""
        if segment.startswith(":"):
            return "{" + segment[1:] + "}"
        return segment

    @staticmethod
    def _path_from_raw(raw: str) -> str:
        """Extract the path portion from a raw URL string."""
        without_query = raw.split("?", 1)[0]
        without_host = re.sub(r"^\{\{[^}]+\}\}", "", without_query)
        without_host = re.sub(r"^https?://[^/]+", "", without_host)
        path = without_host if without_host.startswith("/") else "/" + without_host
        return re.sub(r"/:([a-zA-Z0-9_]+)", r"/{\1}", path)

    @staticmethod
    def _fallback_name(path: str, method: str) -> str:
        """Synthesise a stable name when the request is unnamed."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
        return f"{slug}.{method.lower()}"
