"""Unified structured error handling.

Every tool failure produced by this service is delivered to the MCP client as
JSON text of the shape::

    {"error": {"code": "...", "message": "...", "details": {...}}}

``McpError`` subclasses the SDK 2.x ``ToolError`` so the MCPServer correctly
marks the tool call result as failed (``is_error=True``) while the structured
payload remains the actual message carried in ``content``. No httpx / pydantic /
SDK / Python tracebacks ever reach the client.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

VALIDATION_ERROR = "VALIDATION_ERROR"
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_API_ERROR = "BACKEND_API_ERROR"
BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
INTERNAL_ERROR = "INTERNAL_ERROR"


class McpError(ToolError):
    """A tool failure with a structured, client-safe payload as its message."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        payload = {
            "error": {
                "code": code,
                "message": message,
                "details": self.details,
            }
        }
        super().__init__(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    @classmethod
    def from_payload(cls, payload: str) -> "McpError | None":
        """Re-parse a previously serialized payload (used by tests/tools)."""
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None
        err = (data or {}).get("error")
        if not isinstance(err, dict) or "code" not in err:
            return None
        return cls(err["code"], err.get("message", ""), err.get("details") or {})


def validation_error(
    details: dict[str, Any] | None = None,
    message: str = "Invalid arguments for tool",
) -> McpError:
    return McpError(VALIDATION_ERROR, message, details)


def backend_timeout(details: dict[str, Any] | None = None) -> McpError:
    return McpError(
        BACKEND_TIMEOUT,
        "The pet hospital backend did not respond in time",
        details,
    )


def backend_unavailable(details: dict[str, Any] | None = None) -> McpError:
    return McpError(
        BACKEND_UNAVAILABLE,
        "The pet hospital backend is unreachable",
        details,
    )


def backend_api_error(
    status_code: int,
    message: str,
    details: dict[str, Any] | None = None,
) -> McpError:
    merged = dict(details or {})
    merged.setdefault("httpStatus", status_code)
    return McpError(BACKEND_API_ERROR, message, merged)


def backend_invalid_response(details: dict[str, Any] | None = None) -> McpError:
    return McpError(
        BACKEND_INVALID_RESPONSE,
        "The pet hospital backend returned an invalid response",
        details,
    )


def internal_error(message: str = "Unexpected internal error") -> McpError:
    return McpError(INTERNAL_ERROR, message, {})