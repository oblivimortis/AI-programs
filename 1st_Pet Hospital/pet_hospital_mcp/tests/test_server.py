"""End-to-end HTTP tests against the stateless MCP app (no real backend).

/health is exercised with a plain ASGI transport; the /mcp stateless flow uses
LifespanManager because the StreamableHTTP manager initializes its machinery on
lifespan startup (as uvicorn does in production).
"""

from __future__ import annotations

import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.server import MCP_PROTOCOL_VERSION, create_app
from tests.conftest import SAMPLE_DATA, envelope, make_client


@pytest.fixture
async def app():
    """The stateless MCP app with a mocked backend client."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"code": 200, "message": "ok"})
        if request.url.path == "/api/v1/pets":
            return httpx.Response(200, json=envelope(SAMPLE_DATA))
        return httpx.Response(404)

    client = make_client(handler)
    settings = Settings()
    return create_app(settings, client=client)


async def test_health_endpoint(app):
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["service"] == "pet-hospital-mcp"
            assert body["upstream"] == "reachable"
            assert body["protocolVersion"] == MCP_PROTOCOL_VERSION


async def test_health_defaults_reachable_false_when_backend_down():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)
    app = create_app(Settings(), client=client)
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http:
            body = (await http.get("/health")).json()
            assert body["upstream"] == "unreachable"


async def test_stateless_tools_list_discovery(app):
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            resp = await client.post(
                "/mcp",
                headers={"Mcp-Method": "tools/list", "Accept": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["jsonrpc"] == "2.0"
            tools = body["result"]["tools"]
            assert [t["name"] for t in tools] == ["list_pets"]
            schema = tools[0]["inputSchema"]
            assert schema["type"] == "object"
            assert "params" in schema.get("properties", {})


async def test_stateless_tools_call_success(app):
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            resp = await client.post(
                "/mcp",
                headers={
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "list_pets",
                    "Accept": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "list_pets",
                        "arguments": {"params": {"species": "犬", "pageSize": 5}},
                    },
                },
            )
            assert resp.status_code == 200
            result = resp.json()["result"]
            assert result.get("isError") is False
            content = json.loads(result["content"][0]["text"])
            assert set(content) == {"data"}
            assert content["data"]["items"][0]["name"] == "旺财"


async def test_stateless_tools_call_invalid_input_returns_structured_error(app):
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            resp = await client.post(
                "/mcp",
                headers={
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "list_pets",
                    "Accept": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "list_pets",
                        "arguments": {"params": {"species": "恐龙", "min": 5, "max": 1}},
                    },
                },
            )
            result = resp.json()["result"]
            assert result.get("isError") is True  # SDK 2.x marks ToolError failures
            # SDK 2.x frames a tool failure as "Error executing tool <name>: <message>";
            # the unified payload is the JSON object that follows that prefix.
            text = result["content"][0]["text"]
            assert text.startswith("Error executing tool list_pets: ")
            payload = json.loads(text[text.index("{") :])
            assert payload["error"]["code"] == "VALIDATION_ERROR"
            fields = {e["field"] for e in payload["error"]["details"]["errors"]}
            assert "species" in fields


async def test_stateless_call_never_uses_initialize_or_session(app):
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            # A raw initialize attempt must NOT be honored (no session created).
            resp = await client.post(
                "/mcp",
                headers={
                    "Mcp-Method": "initialize",
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert resp.status_code < 500  # rejected, never 200-with-session
            assert "Mcp-Session-Id" not in resp.headers



