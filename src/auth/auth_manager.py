"""Authentication / token lifecycle manager.

Centralises login, token caching, expiry handling, refresh, and logout so the
adapters never deal with credentials directly - they just ask the manager for a
valid ``Authorization`` header. This keeps auth logic in one place and makes it
easy to swap the auth scheme for a real ERP.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from src.config.settings import Settings
from src.observability.logging import get_logger

_log = get_logger("auth")


class AuthError(RuntimeError):
    """Raised when authentication against the ERP fails."""


class AuthManager:
    """Manages a bearer token's lifecycle for the ERP backend.

    The manager is concurrency-safe: simultaneous callers that need a token
    while none is cached will share a single login request.
    """

    # Refresh slightly before actual expiry to avoid using a just-expired token.
    _EXPIRY_SKEW_SECONDS = 30.0

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """True when credentials are configured (auth should be attempted)."""
        return self._settings.auth_enabled

    async def auth_headers(self) -> dict[str, str]:
        """Return an ``Authorization`` header, logging in/refreshing as needed.

        Returns an empty dict when auth is disabled (anonymous access).
        """
        if not self.enabled:
            return {}
        token = await self._get_valid_token()
        return {"Authorization": f"Bearer {token}"}

    async def _get_valid_token(self) -> str:
        """Return a cached token if still valid, otherwise (re)login."""
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        async with self._lock:
            # Re-check inside the lock in case another coroutine logged in.
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            await self._login()
            assert self._token is not None
            return self._token

    async def _login(self) -> None:
        """Perform a login request and cache the returned token."""
        url = self._settings.erp_auth_login_path
        payload = {
            "username": self._settings.erp_username,
            "password": self._settings.erp_password,
        }
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("login_failed", error=str(exc))
            raise AuthError(f"ERP login failed: {exc}") from exc

        data = response.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise AuthError("ERP login response did not contain an access token")
        expires_in = float(data.get("expires_in", 3600))
        self._token = token
        self._expires_at = time.monotonic() + max(0.0, expires_in - self._EXPIRY_SKEW_SECONDS)
        _log.info("login_ok", expires_in=expires_in)

    async def invalidate(self) -> None:
        """Forget the cached token (forces re-login on next request)."""
        async with self._lock:
            self._token = None
            self._expires_at = 0.0

    async def logout(self) -> None:
        """Best-effort logout; always clears the local token afterwards."""
        if self.enabled and self._token:
            try:
                await self._client.post(self._settings.erp_auth_logout_path)
            except httpx.HTTPError as exc:  # logout failure is non-fatal
                _log.warning("logout_failed", error=str(exc))
        await self.invalidate()
