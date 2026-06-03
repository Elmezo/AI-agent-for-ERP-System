"""Adapter composition root.

Builds the configured ``ERPAdapter`` together with its shared HTTP client and
auth manager. This is the single place that knows which concrete adapter is in
use; everything else depends on the ``ERPAdapter`` protocol.
"""

from __future__ import annotations

import httpx

from src.adapters.base import ERPAdapter
from src.adapters.mock_adapter import MockERPAdapter
from src.adapters.real_adapter import RealERPAdapter
from src.auth.auth_manager import AuthManager
from src.config.settings import AdapterKind, Settings


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """Create a shared async HTTP client bound to the ERP base URL."""
    return httpx.AsyncClient(
        base_url=settings.erp_base_url,
        timeout=settings.erp_timeout_seconds,
    )


def build_adapter(settings: Settings, client: httpx.AsyncClient | None = None) -> ERPAdapter:
    """Construct the adapter selected by ``settings.erp_adapter``.

    Args:
        settings: Application settings.
        client: Optional pre-built HTTP client (useful for tests). When omitted,
            a client bound to ``ERP_BASE_URL`` is created.
    """
    http_client = client or build_http_client(settings)
    auth = AuthManager(settings, http_client)
    if settings.erp_adapter is AdapterKind.REAL:
        return RealERPAdapter(settings, http_client, auth)
    return MockERPAdapter(settings, http_client, auth)
