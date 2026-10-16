"""HTTP client for the Go pet hospital REST API.

The Go service is the only business backend; this MCP service never talks to
anything else and does not modify it. Calls have a hard timeout and a limited
number of retries for network-level failures only (timeouts and connection
errors). 4xx/5xx responses, invalid JSON and unexpected envelopes are mapped
to structured :class:`~pet_hospital_mcp.errors.McpError` instead of leaking
httpx internals.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from pet_hospital_mcp.errors import (
    McpError,
    backend_api_error,
    backend_invalid_response,
    backend_timeout,
    backend_unavailable,
)

logger = logging.getLogger(__name__)

#: Endpoints of the Go backend used by this service.
HEALTH_PATH = "/api/v1/health"
PETS_PATH = "/api/v1/pets"


class RestClient:
    """Thin async HTTP client with timeout, retry and error mapping."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        retry_attempts: int = 2,
        retry_backoff: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry_attempts = max(0, retry_attempts)
        self._backoff = retry_backoff
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- public helpers ----------------------------------------------------

    async def ping(self) -> bool:
        """Best-effort reachability probe of the Go backend /health endpoint."""
        try:
            resp = await self._client.get(HEALTH_PATH)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        """``GET /api/v1/pets`` → the ``data`` object of the response envelope."""
        return await self.get_json(PETS_PATH, params=params)

    # -- internals ---------------------------------------------------------

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a GET with retry + error mapping and return the ``data`` field."""
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts + 1):
            try:
                resp = await self._client.get(path, params=params)
                return await self._parse_envelope(resp)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                if attempt >= self._retry_attempts:
                    raise backend_unavailable(
                        {"detail": f"cannot reach backend at {self._base_url}"}
                    ) from exc
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt >= self._retry_attempts:
                    raise backend_timeout(
                        {"detail": f"request to {path} timed out after {self._timeout}s"}
                    ) from exc
            await asyncio.sleep(self._backoff * (attempt + 1))
        raise backend_unavailable({"detail": f"request to {path} failed"}) from last_error

    async def _parse_envelope(self, resp: httpx.Response) -> dict[str, Any]:
        """Turn a Go REST response into its ``data`` object or a structured error."""
        if resp.status_code < 200 or resp.status_code >= 300:
            env = self._safe_json(resp)
            message = (env or {}).get("message") if isinstance(env, dict) else None
            raise backend_api_error(
                resp.status_code,
                message or f"backend returned HTTP {resp.status_code}",
                {"httpStatus": resp.status_code},
            )

        try:
            env = resp.json()
        except (ValueError, TypeError):
            raise backend_invalid_response(
                {"detail": "response body is not valid JSON"}
            ) from None

        if not isinstance(env, dict) or "data" not in env:
            raise backend_invalid_response(
                {"detail": "response envelope is missing the 'data' field"}
            )
        data = env.get("data")
        if not isinstance(data, dict):
            raise backend_invalid_response(
                {"detail": "response 'data' field is not an object"}
            )
        return data

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any] | None:
        try:
            data = resp.json()
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None