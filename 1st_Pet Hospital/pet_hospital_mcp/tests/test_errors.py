"""Tests for the unified structured error model."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pet_hospital_mcp.errors import (
    BACKEND_API_ERROR,
    VALIDATION_ERROR,
    McpError,
    backend_unavailable,
    validation_error,
)


def test_mcp_error_is_a_toolerror():
    # SDK 2.x marks ToolError-derived failures as is_error=True.
    err = validation_error({"errors": [{"field": "species", "error": "x"}]})
    assert isinstance(err, ToolError)
    assert err.code == VALIDATION_ERROR


def test_payload_shape_roundtrip():
    err = validation_error(
        {"errors": [{"field": "min", "error": "must be <= max"}]},
        message="Invalid arguments for tool",
    )
    payload = json.loads(str(err))
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == VALIDATION_ERROR
    assert payload["error"]["message"] == "Invalid arguments for tool"
    assert payload["error"]["details"]["errors"][0]["field"] == "min"

    reparsed = McpError.from_payload(str(err))
    assert reparsed is not None
    assert reparsed.code == VALIDATION_ERROR
    assert reparsed.message == "Invalid arguments for tool"


def test_payload_is_compact_json_without_tracebacks():
    err = backend_unavailable({"detail": "cannot reach backend at http://x"})
    text = str(err)
    assert "Traceback" not in text
    assert "httpx" not in text
    assert "pet_hospital_mcp" not in text
    assert json.loads(text)["error"]["code"] == "BACKEND_UNAVAILABLE"


def test_from_payload_rejects_garbage():
    assert McpError.from_payload("not json") is None
    assert McpError.from_payload('{"foo": 1}') is None


def test_factory_defaults():
    err = validation_error()
    assert err.code == VALIDATION_ERROR
    assert err.details == {}