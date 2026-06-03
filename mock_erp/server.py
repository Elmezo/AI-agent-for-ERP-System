"""Temporary FastAPI mock ERP backend.

Serves the same endpoints (and response shapes) the agent expects from a real
ERP, backed by the dummy JSON files in ``mock_erp/data``. Replace those files
(or point ``ERP_ADAPTER=real`` at your real backend) without touching the agent.

Run with:
    python -m mock_erp.server
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

DATA_DIR = Path(__file__).parent / "data"


def _load(name: str) -> Any:
    """Load a JSON data file from the data directory."""
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _load_all() -> dict[str, Any]:
    """Load all dummy datasets into memory once at startup."""
    return {
        "people": _load("people.json"),
        "org_units": _load("org_units.json"),
        "systems": _load("systems.json"),
        "datasets": _load("datasets.json"),
        "stakeholders": _load("stakeholders.json"),
        "interfaces": _load("interfaces.json"),
    }


app = FastAPI(title="Mock ERP", version="1.0.0")
DB: dict[str, Any] = _load_all()


def _by_id(collection: list[dict[str, Any]], item_id: int) -> dict[str, Any] | None:
    """Find a record by its integer id."""
    return next((r for r in collection if r.get("id") == item_id), None)


def _search(collection: list[dict[str, Any]], q: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Case-insensitive substring search across the given fields."""
    needle = (q or "").strip().lower()
    if not needle:
        return []
    out = []
    for record in collection:
        for field in fields:
            value = record.get(field)
            if value is not None and needle in str(value).lower():
                out.append(record)
                break
    return out


# --- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    """Login payload accepted by the mock auth endpoint."""

    username: str
    password: str


@app.post("/auth/login")
async def login(body: LoginRequest) -> dict[str, Any]:
    """Issue a fake bearer token. The mock accepts any non-empty credentials."""
    if not body.username or not body.password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"access_token": f"mock-token-for-{body.username}", "token_type": "bearer", "expires_in": 3600}


@app.post("/auth/logout")
async def logout() -> dict[str, str]:
    """Acknowledge logout (stateless mock)."""
    return {"status": "logged_out"}


# --- People ----------------------------------------------------------------
@app.get("/api/people")
async def list_people() -> list[dict[str, Any]]:
    """List all employees."""
    return DB["people"]


@app.get("/api/people/search")
async def search_people(q: str = Query(...)) -> list[dict[str, Any]]:
    """Search employees by name or email."""
    return _search(DB["people"], q, ("name", "email", "title"))


@app.get("/api/people/{id}")
async def get_person(id: int) -> dict[str, Any]:
    """Get a single employee by id."""
    record = _by_id(DB["people"], id)
    if record is None:
        raise HTTPException(status_code=404, detail="person not found")
    return record


# --- Org units -------------------------------------------------------------
@app.get("/api/org-units")
async def list_org_units() -> list[dict[str, Any]]:
    """List all organizational units."""
    return DB["org_units"]


@app.get("/api/org-units/search")
async def search_org_units(q: str = Query(...)) -> list[dict[str, Any]]:
    """Search organizational units by name or code."""
    return _search(DB["org_units"], q, ("name", "code"))


@app.get("/api/org-units/{id}")
async def get_org_unit(id: int) -> dict[str, Any]:
    """Get a single organizational unit by id."""
    record = _by_id(DB["org_units"], id)
    if record is None:
        raise HTTPException(status_code=404, detail="org unit not found")
    return record


# --- Systems ---------------------------------------------------------------
@app.get("/api/systems")
async def list_systems() -> list[dict[str, Any]]:
    """List all systems."""
    return DB["systems"]


@app.get("/api/systems/search")
async def search_systems(q: str = Query(...)) -> list[dict[str, Any]]:
    """Search systems by name or description."""
    return _search(DB["systems"], q, ("name", "description"))


@app.get("/api/systems/{id}")
async def get_system(id: int) -> dict[str, Any]:
    """Get a single system by id."""
    record = _by_id(DB["systems"], id)
    if record is None:
        raise HTTPException(status_code=404, detail="system not found")
    return record


@app.get("/api/systems/{id}/stakeholders")
async def system_stakeholders(id: int) -> list[dict[str, Any]]:
    """Return the stakeholders of a system (person summary + role)."""
    if _by_id(DB["systems"], id) is None:
        raise HTTPException(status_code=404, detail="system not found")
    entries = DB["stakeholders"].get(str(id), [])
    enriched: list[dict[str, Any]] = []
    for entry in entries:
        person = _by_id(DB["people"], entry["personId"])
        enriched.append(
            {
                "personId": entry["personId"],
                "name": person["name"] if person else None,
                "title": person["title"] if person else None,
                "role": entry["role"],
            }
        )
    return enriched


@app.get("/api/systems/{id}/interfaces")
async def system_interfaces(id: int) -> list[dict[str, Any]]:
    """Return the interfaces connecting a system to other systems."""
    if _by_id(DB["systems"], id) is None:
        raise HTTPException(status_code=404, detail="system not found")
    entries = DB["interfaces"].get(str(id), [])
    enriched: list[dict[str, Any]] = []
    for entry in entries:
        target = _by_id(DB["systems"], entry["targetSystemId"])
        enriched.append(
            {
                "targetSystemId": entry["targetSystemId"],
                "targetSystemName": target["name"] if target else None,
                "type": entry["type"],
                "direction": entry["direction"],
            }
        )
    return enriched


# --- Datasets --------------------------------------------------------------
@app.get("/api/datasets")
async def list_datasets() -> list[dict[str, Any]]:
    """List all datasets."""
    return DB["datasets"]


@app.get("/api/datasets/search")
async def search_datasets(q: str = Query(...)) -> list[dict[str, Any]]:
    """Search datasets by name or description."""
    return _search(DB["datasets"], q, ("name", "description"))


@app.get("/api/datasets/{id}")
async def get_dataset(id: int) -> dict[str, Any]:
    """Get a single dataset by id."""
    record = _by_id(DB["datasets"], id)
    if record is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return record


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


def main() -> None:
    """Entry point for ``python -m mock_erp.server``."""
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
