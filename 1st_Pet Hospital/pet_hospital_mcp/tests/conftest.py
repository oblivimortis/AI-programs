"""Shared test fixtures and helpers. Tests never touch the real Go service."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import RestClient

#: A valid Go envelope's ``data`` payload used across tests.
SAMPLE_DATA: dict[str, Any] = {
    "items": [
        {
            "id": "PET-000001",
            "name": "旺财",
            "species": "犬",
            "breed": "金毛",
            "gender": "公",
            "ageMonths": 30,
            "color": "金色",
            "chipNo": "CHIP-1",
            "ownerName": "李先生",
            "ownerPhone": "13800000000",
            "ownerAddr": "某市某区某路1号",
            "doctor": "王医生",
            "disease": "皮炎",
            "status": "已康复",
            "allergy": "无",
            "records": None,
            "charges": [
                {"id": "CH-1", "item": "挂号费", "category": "检查", "amount": 20.0,
                 "doctor": "王医生", "date": "2026-01-01"}
            ],
            "totalCost": 120.5,
            "visitCount": 3,
            "createdAt": "2026-01-01T10:00:00Z",
            "updatedAt": "2026-02-01T10:00:00Z",
        }
    ],
    "total": 1,
    "page": 1,
    "pageSize": 20,
    "totalPages": 1,
    "totalCost": 120.5,
}


def envelope(data: Any, code: int = 200, message: str = "ok") -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def make_client(handler: Callable[[httpx.Request], Awaitable[httpx.Response]], **kw: Any) -> RestClient:
    """Build a RestClient over a MockTransport with retries disabled by default."""
    kw.setdefault("base_url", "http://backend.test")
    kw.setdefault("retry_attempts", 0)
    kw.setdefault("retry_backoff", 0.0)
    return RestClient(transport=httpx.MockTransport(handler), **kw)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def mock_ok_client() -> RestClient:
    """Client returning SAMPLE_DATA for GET /api/v1/pets."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/pets"
        return httpx.Response(200, json=envelope(SAMPLE_DATA))

    return make_client(handler)


@pytest.fixture
async def anyio_backend():  # pragma: no cover - marker only
    pass