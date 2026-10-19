"""Tests for RestClient: parameter forwarding, timeouts, retries, mapping."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from pet_hospital_mcp.errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    McpError,
)
from pet_hospital_mcp.rest_client import RestClient
from tests.conftest import envelope, make_client


async def test_forwards_all_query_params_and_request_path():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200, json=envelope({"items": []}))

    client = make_client(handler)
    await client.list_pets(
        {
            "q": "皮肤",
            "name": "旺财",
            "ownerName": "李",
            "ownerPhone": "138",
            "species": "犬",
            "doctor": "王医生",
            "disease": "皮炎",
            "status": "已康复",
            "min": 1.5,
            "max": 99.9,
            "sortBy": "totalCost",
            "order": "desc",
            "page": 2,
            "pageSize": 25,
        }
    )

    url = seen["url"]
    assert seen["method"] == "GET"
    assert urlparse(url).path == "/api/v1/pets"
    qs = parse_qs(urlparse(url).query)
    assert qs == {
        "q": ["皮肤"], "name": ["旺财"], "ownerName": ["李"], "ownerPhone": ["138"],
        "species": ["犬"], "doctor": ["王医生"], "disease": ["皮炎"], "status": ["已康复"],
        "min": ["1.5"], "max": ["99.9"], "sortBy": ["totalCost"], "order": ["desc"],
        "page": ["2"], "pageSize": ["25"],
    }


async def test_returns_data_field_from_envelope():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 200, "message": "ok", "data": {"items": [], "total": 0}}
        )

    data = await make_client(handler).list_pets({})
    assert data == {"items": [], "total": 0}


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_backend_4xx_5xx_maps_to_backend_api_error(status):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"code": status, "message": "boom", "data": None}
        )

    client = make_client(handler)
    with pytest.raises(McpError) as ei:
        await client.list_pets({})
    assert ei.value.code == BACKEND_API_ERROR
    assert "boom" in ei.value.message
    assert ei.value.details.get("httpStatus") == status


async def test_timeout_maps_to_backend_timeout_and_retries():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_client(handler, retry_attempts=2, retry_backoff=0.0)
    with pytest.raises(McpError) as ei:
        await client.list_pets({})
    assert ei.value.code == BACKEND_TIMEOUT
    assert calls["n"] == 3  # 1 initial + 2 retries


async def test_connection_error_maps_to_backend_unavailable_and_retries():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler, retry_attempts=1, retry_backoff=0.0)
    with pytest.raises(McpError) as ei:
        await client.list_pets({})
    assert ei.value.code == BACKEND_UNAVAILABLE
    assert calls["n"] == 2


async def test_no_retry_on_http_error_status():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"message": "boom"})

    client = make_client(handler, retry_attempts=3, retry_backoff=0.0)
    with pytest.raises(McpError) as ei:
        await client.list_pets({})
    assert ei.value.code == BACKEND_API_ERROR
    assert calls["n"] == 1


async def test_invalid_json_maps_to_backend_invalid_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(McpError) as ei:
        await make_client(handler).list_pets({})
    assert ei.value.code == BACKEND_INVALID_RESPONSE
    assert "JSON" in ei.value.details.get("detail", "")


async def test_missing_data_field_maps_to_backend_invalid_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "message": "ok"})

    with pytest.raises(McpError) as ei:
        await make_client(handler).list_pets({})
    assert ei.value.code == BACKEND_INVALID_RESPONSE


async def test_ping_reports_reachability():
    async def bad(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await make_client(bad).ping() is False

    async def good(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        return httpx.Response(200, json={"code": 200, "message": "ok"})

    assert await make_client(good).ping() is True