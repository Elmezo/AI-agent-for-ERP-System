"""Generate ``config/api_registry.json`` from one or more discovery sources.

Usage:
    python -m src.discovery.generator --source openapi config/sources/openapi.json
    python -m src.discovery.generator --source postman config/sources/postman_collection.json
    python -m src.discovery.generator --source manual  config/api_registry.json [--out config/api_registry.json]

Sources are pluggable: each implements :class:`~src.discovery.base.ApiSource`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.discovery.base import ApiSource
from src.discovery.manual_source import ManualSource
from src.discovery.openapi_source import OpenApiSource
from src.discovery.postman_source import PostmanSource
from src.models.api import ApiEndpoint

#: Registry of available discovery source implementations.
SOURCES: dict[str, type[ApiSource]] = {
    OpenApiSource.name: OpenApiSource,
    PostmanSource.name: PostmanSource,
    ManualSource.name: ManualSource,
}


def build_source(kind: str, path: Path) -> ApiSource:
    """Instantiate a discovery source by kind."""
    try:
        source_cls = SOURCES[kind]
    except KeyError as exc:
        raise ValueError(
            f"Unknown source '{kind}'. Available: {', '.join(sorted(SOURCES))}"
        ) from exc
    return source_cls(path)


def endpoints_to_registry(endpoints: list[ApiEndpoint]) -> dict[str, dict]:
    """Serialise endpoints into the ``api_registry.json`` shape."""
    registry: dict[str, dict] = {}
    for ep in endpoints:
        entry: dict = {"url": ep.url, "method": ep.method.value}
        if ep.path_params:
            entry["path_params"] = list(ep.path_params)
        if ep.query_params:
            entry["query_params"] = list(ep.query_params)
        if ep.body_params:
            entry["body_params"] = list(ep.body_params)
        if ep.facet:
            entry["facet"] = ep.facet
        if ep.description:
            entry["description"] = ep.description
        registry[ep.name] = entry
    return registry


def generate(kind: str, source_path: Path, out_path: Path) -> dict[str, dict]:
    """Run discovery and write the resulting registry to ``out_path``."""
    source = build_source(kind, source_path)
    endpoints = source.load()
    registry = endpoints_to_registry(endpoints)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return registry


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate api_registry.json from a discovery source.")
    parser.add_argument("--source", required=True, choices=sorted(SOURCES), help="Discovery source kind.")
    parser.add_argument("input", help="Path to the source file (OpenAPI/Postman/manual registry).")
    parser.add_argument(
        "--out", default="config/api_registry.json", help="Output registry path."
    )
    args = parser.parse_args(argv)

    registry = generate(args.source, Path(args.input), Path(args.out))
    print(f"Wrote {len(registry)} endpoints to {args.out} (source={args.source}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
