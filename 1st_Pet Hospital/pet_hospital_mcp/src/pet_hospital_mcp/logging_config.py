"""JSON logging with recursive redaction of sensitive fields.

Log records are single-line JSON documents, for example::

    {"timestamp": "2026-10-08T09:30:00.123+08:00", "level": "INFO",
     "logger": "pet_hospital_mcp.server", "message": "tool_call",
     "tool_name": "list_pets", "params": {"ownerPhone": "***", ...},
     "status": "ok", "duration_ms": 4.2}

Sensitive keys (``ownerPhone``, ``ownerAddr``, ``chipNo`` and their
snake_case spellings) are replaced recursively wherever they appear in
log payloads, so full sensitive data never reaches the logs.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

SENSITIVE_KEYS = {
    "ownerphone",
    "owneraddr",
    "chipno",
    "owner_phone",
    "owner_addr",
    "chip_no",
}
REDACTED = "***"


def redact(value: Any) -> Any:
    """Recursively replace sensitive values with ``***`` (keys are kept)."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if key.lower() in SENSITIVE_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class JsonFormatter(logging.Formatter):
    """Emit each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "timestamp": _now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "_fields", None)
        if isinstance(fields, dict):
            out.update(redact(fields))
        if record.exc_info and record.exc_info[0] is not None:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with the JSON formatter on stderr."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    params: dict[str, Any] | None,
    status: str,
    duration_ms: float,
) -> None:
    """Log a tool invocation with the fields required by the spec."""
    logger.info(
        "tool_call",
        extra={
            "_fields": {
                "tool_name": tool_name,
                "params": redact(dict(params or {})),
                "status": status,
                "duration_ms": round(duration_ms, 3),
            }
        },
    )