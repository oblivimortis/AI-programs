"""Tests for the list_pets tool: strict validation, output parsing, error mapping."""

from __future__ import annotations

import json
import math

import httpx
import pytest
from pydantic import ValidationError

from pet_hospital_mcp.errors import (
    BACKEND_INVALID_RESPONSE,
    BACKEND_UNAVAILABLE,
    VALIDATION_ERROR,
    McpError,
)
from pet_hospital_mcp.tools.list_pets import ListPetsInput, handle
from tests.conftest import SAMPLE_DATA, envelope, make_client


# --------------------------------------------------------------------------
# Input validation (unit, no HTTP)
# --------------------------------------------------------------------------


def test_valid_minimal_input():
    inp = ListPetsInput.model_validate({})
    assert inp.to_params() == {"page": 1, "pageSize": 20}


def test_valid_full_input():
    inp = ListPetsInput.model_validate(
        {
            "q": "皮肤", "name": "旺财", "ownerName": "李", "ownerPhone": "138",
            "species": "犬", "doctor": "王医生", "disease": "皮炎", "status": "已康复",
            "min": 1.0, "max": 500.0, "sortBy": "totalCost", "order": "desc",
            "page": 2, "pageSize": 50,
        }
    )
    p = inp.to_params()
    assert p["species"] == "犬" and p["status"] == "已康复"
    assert p["sortBy"] == "totalCost" and p["order"] == "desc"
    assert p["page"] == 2 and p["pageSize"] == 50


@pytest.mark.parametrize(
    "field,value",
    [
        ("species", "恐龙"),
        ("status", "未知状态"),
        ("sortBy", "password"),
        ("order", "sideways"),
    ],
)
def test_rejects_unknown_enum_values(field, value):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({field: value})


@pytest.mark.parametrize("page", [0, -1, 1.5, "1"])
def test_rejects_invalid_page(page):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"page": page})


@pytest.mark.parametrize("page_size", [0, -10, 501, 500.0, "20"])
def test_rejects_invalid_page_size(page_size):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"pageSize": page_size})


@pytest.mark.parametrize("value", [-0.01, -100, math.nan, math.inf, -math.inf])
def test_rejects_negative_and_non_finite_min(value):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"min": value})


def test_rejects_non_finite_max():
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"max": math.inf})


def test_rejects_min_greater_than_max():
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"min": 300.0, "max": 100.0})
    # equal is allowed
    assert ListPetsInput.model_validate({"min": 100.0, "max": 100.0})


@pytest.mark.parametrize("field,value", [("q", 123), ("name", 1), ("page", "abc")])
def test_rejects_type_incorrect_inputs(field, value):
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({field: value})


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"secretQuery": "x"})


def test_allows_only_supported_parameters():
    supported = set(ListPetsInput.model_fields)
    assert supported == {
        "q", "name", "ownerName", "ownerPhone", "species", "doctor", "disease",
        "status", "min", "max", "sortBy", "order", "page", "pageSize",
    }


# --------------------------------------------------------------------------
# handle(): full flow against a mocked backend
# --------------------------------------------------------------------------


async def test_handle_success_shape(mock_ok_client):
    text = await handle(mock_ok_client, {"species": "犬", "pageSize": 1})
    payload = json.loads(text)
    data = payload["data"]
    assert set(data) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}
    pet = data["items"][0]
    assert pet["name"] == "旺财"
    assert pet["records"] is None          # null accepted
    assert isinstance(pet["charges"], list)  # array accepted


async def test_handle_accepts_records_array_too():
    data = json.loads(json.dumps(SAMPLE_DATA))
    data["items"][0]["records"] = [{"id": "MR-1", "diagnosis": "皮炎"}]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope(data))

    text = await handle(make_client(handler), {})
    pet = json.loads(text)["data"]["items"][0]
    assert pet["records"][0]["diagnosis"] == "皮炎"


async def test_invalid_input_raises_structured_validation_error(mock_ok_client):
    with pytest.raises(McpError) as ei:
        await handle(mock_ok_client, {"species": "恐龙"})
    assert ei.value.code == VALIDATION_ERROR
    payload = json.loads(str(ei.value))
    assert payload["error"]["details"]["errors"][0]["field"] == "species"


async def test_min_max_inconsistency_raises_validation_error(mock_ok_client):
    with pytest.raises(McpError) as ei:
        await handle(mock_ok_client, {"min": 900, "max": 1})
    assert ei.value.code == VALIDATION_ERROR


async def test_backend_response_mismatch_raises_invalid_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope({"items": "not-a-list"}))

    with pytest.raises(McpError) as ei:
        await handle(make_client(handler), {})
    assert ei.value.code == BACKEND_INVALID_RESPONSE
    assert "items" in str(ei.value)


async def test_backend_unreachable_raises_unavailable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(McpError) as ei:
        await handle(make_client(handler), {})
    assert ei.value.code == BACKEND_UNAVAILABLE


async def test_backend_4xx_raises_api_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 500, "message": "db exploded"})

    with pytest.raises(McpError) as ei:
        await handle(make_client(handler), {})
    assert ei.value.code == "BACKEND_API_ERROR"
    assert "db exploded" in ei.value.message