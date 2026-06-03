"""Mock ERP adapter.

Talks to the bundled FastAPI mock server over HTTP. Functionally identical to
the real adapter; the distinction exists so configuration and logging clearly
indicate which backend is in use, and so a future offline/in-process mock could
override behaviour without affecting the rest of the system.
"""

from __future__ import annotations

import httpx

from src.adapters.http_adapter import HttpERPAdapter
from src.auth.auth_manager import AuthManager
from src.config.settings import Settings


class MockERPAdapter(HttpERPAdapter):
    """HTTP adapter pointed at the local mock ERP server."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, auth: AuthManager) -> None:
        super().__init__(settings, client, auth, label="mock")
