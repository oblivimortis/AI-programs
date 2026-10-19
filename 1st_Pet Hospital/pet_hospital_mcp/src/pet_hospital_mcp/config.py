"""Runtime configuration, read from environment variables.

Variables
---------
MCP_HOST:
    Address the MCP HTTP server listens on. Default ``127.0.0.1`` (local only).
MCP_PORT:
    Port the MCP HTTP server listens on. Default ``8002``.
PET_HOSPITAL_BASE_URL:
    Base URL of the Go pet hospital REST API (the only business backend).
    Default ``http://127.0.0.1:8080``.
PET_HOSPITAL_REQUEST_TIMEOUT:
    Per-request timeout in seconds for backend calls. Default ``10``.
PET_HOSPITAL_RETRY_ATTEMPTS:
    Extra retries (on top of the first attempt) for network-level failures
    (timeouts / connection errors). 4xx/5xx are never retried. Default ``2``.
PET_HOSPITAL_RETRY_BACKOFF:
    Base backoff (seconds) between retries, scaled linearly. Default ``0.25``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8002
DEFAULT_BASE_URL = "http://127.0.0.1:8080"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the MCP service."""

    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = DEFAULT_MCP_PORT
    pet_hospital_base_url: str = DEFAULT_BASE_URL
    request_timeout: float = 10.0
    retry_attempts: int = 2
    retry_backoff: float = 0.25

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ``)."""
        env = os.environ if env is None else env
        return cls(
            mcp_host=env.get("MCP_HOST", DEFAULT_MCP_HOST),
            mcp_port=int(env.get("MCP_PORT", str(DEFAULT_MCP_PORT))),
            pet_hospital_base_url=env.get("PET_HOSPITAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            request_timeout=float(env.get("PET_HOSPITAL_REQUEST_TIMEOUT", "10")),
            retry_attempts=int(env.get("PET_HOSPITAL_RETRY_ATTEMPTS", "2")),
            retry_backoff=float(env.get("PET_HOSPITAL_RETRY_BACKOFF", "0.25")),
        )

    @property
    def mcp_endpoint(self) -> str:
        """The streamable HTTP MCP endpoint base URL."""
        return f"http://{self.mcp_host}:{self.mcp_port}/mcp"