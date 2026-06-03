"""Shared HTTP adapter logic for talking to an ERP over HTTP.

Both the mock and real adapters reuse this implementation; they differ only in
configuration (base URL, credentials). Responsibilities:

  * build the request URL from an endpoint template + parameters
  * attach auth headers from the :class:`AuthManager`
  * apply timeouts and bounded retries with exponential backoff (tenacity)
  * convert every outcome into a normalised :class:`ApiResult`
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.adapters.base import ERPAdapter
from src.auth.auth_manager import AuthError, AuthManager
from src.config.settings import Settings
from src.models.api import ApiEndpoint, ApiResult
from src.observability.logging import get_logger

_log = get_logger("adapter")


class _RetryableHTTP(Exception):
    """Internal marker for transient HTTP failures worth retrying."""


class HttpERPAdapter(ERPAdapter):
    """HTTP implementation of :class:`ERPAdapter`.

    Args:
        settings: Application settings (timeouts, retries).
        client: Shared ``httpx.AsyncClient`` (base_url already configured).
        auth: Auth manager providing bearer headers.
        label: Adapter label used in logs ("mock"/"real").
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        auth: AuthManager,
        label: str = "http",
    ) -> None:
        self._settings = settings
        self._client = client
        self._auth = auth
        self._label = label

    async def call(
        self,
        endpoint: ApiEndpoint,
        *,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ApiResult:
        """Execute an endpoint with retries and return a normalised result."""
        path_params = path_params or {}
        query_params = query_params or {}
        url = self._build_url(endpoint, path_params)
        params = self._filter_query(endpoint, query_params)
        start = time.perf_counter()

        try:
            response = await self._send_with_retry(endpoint, url, params, body)
        except AuthError as exc:
            return ApiResult.failure(endpoint.name, f"authentication failed: {exc}")
        except _RetryableHTTP as exc:
            # Retries exhausted on a server error / persistent 401.
            _log.warning("api_retries_exhausted", adapter=self._label, api=endpoint.name, error=str(exc))
            return ApiResult.failure(endpoint.name, f"server error after retries: {exc}")
        except httpx.HTTPError as exc:
            _log.warning("api_transport_error", adapter=self._label, api=endpoint.name, error=str(exc))
            return ApiResult.failure(endpoint.name, f"transport error: {exc}")

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        return self._to_result(endpoint, response, elapsed_ms)

    async def _send_with_retry(
        self,
        endpoint: ApiEndpoint,
        url: str,
        params: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> httpx.Response:
        """Send the request, retrying transient failures and re-auth on 401."""
        attempts = max(1, self._settings.erp_max_retries)
        reauthed = False

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=0.3, max=4),
            retry=retry_if_exception_type((_RetryableHTTP, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                headers = await self._auth.auth_headers()
                response = await self._client.request(
                    endpoint.method.value,
                    url,
                    params=params or None,
                    json=body or None,
                    headers=headers,
                )
                if response.status_code == 401 and self._auth.enabled and not reauthed:
                    reauthed = True
                    await self._auth.invalidate()
                    raise _RetryableHTTP("401 - re-authenticating")
                if response.status_code >= 500:
                    raise _RetryableHTTP(f"server error {response.status_code}")
                return response
        raise httpx.HTTPError("retry loop exhausted")  # pragma: no cover

    def _to_result(self, endpoint: ApiEndpoint, response: httpx.Response, elapsed_ms: float) -> ApiResult:
        """Translate an HTTP response into an ``ApiResult``."""
        # 404 means "no such record" -> treat as empty data, not a hard error.
        if response.status_code == 404:
            return ApiResult.success(
                endpoint.name, None, status_code=404, elapsed_ms=elapsed_ms
            )
        if response.status_code >= 400:
            detail = self._error_detail(response)
            _log.warning(
                "api_error", adapter=self._label, api=endpoint.name,
                status=response.status_code, detail=detail,
            )
            return ApiResult.failure(
                endpoint.name, detail, status_code=response.status_code, elapsed_ms=elapsed_ms
            )
        try:
            data = response.json()
        except ValueError:
            data = response.text
        return ApiResult.success(
            endpoint.name, data, status_code=response.status_code, elapsed_ms=elapsed_ms
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract a human-readable error message from an error response."""
        try:
            payload = response.json()
            if isinstance(payload, dict) and "detail" in payload:
                return f"HTTP {response.status_code}: {payload['detail']}"
        except ValueError:
            pass
        return f"HTTP {response.status_code}"

    @staticmethod
    def _build_url(endpoint: ApiEndpoint, path_params: dict[str, Any]) -> str:
        """Interpolate path parameters into the endpoint template."""
        try:
            return endpoint.url.format(**path_params)
        except KeyError as exc:
            raise ValueError(
                f"missing path parameter {exc} for endpoint {endpoint.name}"
            ) from exc

    @staticmethod
    def _filter_query(endpoint: ApiEndpoint, query_params: dict[str, Any]) -> dict[str, Any]:
        """Keep only declared query params with non-null values."""
        allowed = set(endpoint.query_params)
        return {
            k: v
            for k, v in query_params.items()
            if (not allowed or k in allowed) and v is not None
        }

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
