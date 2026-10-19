"""Tests for config parsing and log redaction."""

from __future__ import annotations

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.logging_config import log_tool_call, redact


def test_settings_from_env_defaults():
    s = Settings.from_env({})
    assert s.mcp_host == "127.0.0.1"
    assert s.mcp_port == 8002
    assert s.pet_hospital_base_url == "http://127.0.0.1:8080"


def test_settings_from_env_overrides():
    s = Settings.from_env(
        {
            "MCP_HOST": "0.0.0.0",
            "MCP_PORT": "9000",
            "PET_HOSPITAL_BASE_URL": "http://127.0.0.1:9999/",
            "PET_HOSPITAL_REQUEST_TIMEOUT": "3",
            "PET_HOSPITAL_RETRY_ATTEMPTS": "1",
        }
    )
    assert s.mcp_host == "0.0.0.0"
    assert s.mcp_port == 9000
    assert s.pet_hospital_base_url == "http://127.0.0.1:9999"
    assert s.request_timeout == 3.0
    assert s.retry_attempts == 1
    assert s.mcp_endpoint == "http://0.0.0.0:9000/mcp"


def test_redact_sensitive_keys_recursively():
    data = {
        "list": [
            {"ownerPhone": "13800138000", "ownerAddr": "某路1号", "chipNo": "CHIP-9"},
            {"owner_phone": "139", "owner_addr": "某路2号", "chip_no": "C2"},
        ],
        "name": "旺财",
        "nested": {"deep": {"ownerPhone": "x"}},
    }
    out = redact(data)
    assert out["name"] == "旺财"
    assert out["list"][0] == {"ownerPhone": "***", "ownerAddr": "***", "chipNo": "***"}
    assert out["list"][1] == {"owner_phone": "***", "owner_addr": "***", "chip_no": "***"}
    assert out["nested"]["deep"] == {"ownerPhone": "***"}
    # source values never mutated
    assert data["list"][0]["ownerPhone"] == "13800138000"


def test_log_tool_call_emits_required_fields(caplog):
    import logging

    logger = logging.getLogger("test.logger")
    with caplog.at_level(logging.INFO, logger="test.logger"):
        log_tool_call(
            logger,
            "list_pets",
            {"ownerPhone": "13800138000", "q": "猫"},
            "ok",
            1.23,
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    fields = record._fields
    assert fields["tool_name"] == "list_pets"
    assert fields["status"] == "ok"
    assert fields["duration_ms"] == 1.23
    assert fields["params"]["ownerPhone"] == "***"
    assert fields["params"]["q"] == "猫"