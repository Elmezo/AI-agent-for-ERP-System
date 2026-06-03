"""Real ERP adapter.

Talks to the production ERP over HTTP using the configured base URL and
credentials. Identical transport behaviour to the mock adapter - only
configuration differs - which is exactly what makes switching backends a
config-only change.
"""

from __future__ import annotations

import httpx

from src.adapters.http_adapter import HttpERPAdapter
from src.auth.auth_manager import AuthManager
from src.config.settings import Settings


class RealERPAdapter(HttpERPAdapter):
    """HTTP adapter pointed at the real ERP backend."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, auth: AuthManager) -> None:
        super().__init__(settings, client, auth, label="real")
