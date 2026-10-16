"""The ``list_pets`` tool: strict adapter for ``GET /api/v1/pets``.

Only the query parameters supported by the real Go backend are exposed; no
private adapter parameters are added. Validation is deliberately stricter than
the (lenient) backend: unknown fields, NaN / Infinity, type mismatches and
out-of-range values are rejected up front with a structured
``VALIDATION_ERROR`` payload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pet_hospital_mcp.errors import (
    McpError,
    backend_invalid_response,
    validation_error,
)
from pet_hospital_mcp.rest_client import RestClient

logger = logging.getLogger(__name__)

#: Real allowed values reported by the Go backend (GET /api/v1/meta).
SPECIES = ("犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他")
STATUS = ("待就诊", "就诊中", "住院中", "已康复", "慢性病随访")
SORT_BY = (
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
)
ORDER = ("asc", "desc")

SPECIES_LITERAL = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
STATUS_LITERAL = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SORT_BY_LITERAL = Literal[
    "id", "name", "ownerName", "species", "doctor", "disease", "status",
    "totalCost", "visitCount", "createdAt", "updatedAt",
]


class ListPetsInput(BaseModel):
    """Strict input model. Extra fields and coercions are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    q: str | None = None
    name: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    species: SPECIES_LITERAL | None = None
    doctor: str | None = None
    disease: str | None = None
    status: STATUS_LITERAL | None = None
    min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sortBy: SORT_BY_LITERAL | None = None
    order: Literal["asc", "desc"] | None = None
    page: int | None = Field(default=1, ge=1)
    pageSize: int | None = Field(default=20, ge=1, le=500)

    @model_validator(mode="after")
    def _min_must_not_exceed_max(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be less than or equal to max")
        return self

    def to_params(self) -> dict[str, Any]:
        """Query parameters to forward to ``GET /api/v1/pets``."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


# --------------------------------------------------------------------------
# Success output models. ``records`` / ``charges`` may be null OR arrays in the
# real Go response; the Optional list types below accept both.
# --------------------------------------------------------------------------


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    visitDate: str | None = None
    doctor: str | None = None
    diagnosis: str | None = None
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weightKg: float | None = None
    temperature: float | None = None
    followUp: str | None = None
    charge: float | None = None
    createdAt: str | None = None


class _Charge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    item: str | None = None
    category: str | None = None
    amount: float | None = None
    doctor: str | None = None
    date: str | None = None


class Pet(BaseModel):
    """A single pet record as returned inside ``data.items``."""

    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    name: str | None = None
    species: str | None = None
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    ownerAddr: str | None = None
    doctor: str | None = None
    disease: str | None = None
    status: str | None = None
    allergy: str | None = None
    records: list[_Record] | None = None
    charges: list[_Charge] | None = None
    totalCost: float | None = None
    visitCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class ListPetsResult(BaseModel):
    """Successful output, mirroring the ``data`` object of the Go response."""

    model_config = ConfigDict(extra="ignore")
    items: list[Pet]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float


def _format_validation_error(exc: ValidationError) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        errors.append({"field": loc, "error": err.get("msg", "invalid value")})
    return {"errors": errors}


async def handle(client: RestClient, raw_args: dict[str, Any] | None) -> str:
    """Validate, call the backend, and serialize the result.

    Always returns a JSON string: ``{"data": {...}}`` on success, or raises
    :class:`~pet_hospital_mcp.errors.McpError` whose message is the unified
    ``{"error": {...}}`` payload.
    """
    try:
        params = ListPetsInput.model_validate(raw_args or {})
    except ValidationError as exc:
        raise validation_error(_format_validation_error(exc)) from None

    data = await client.list_pets(params.to_params())

    try:
        result = ListPetsResult.model_validate(data)
    except ValidationError as exc:
        raise backend_invalid_response(_format_validation_error(exc)) from None

    return json.dumps(
        {"data": result.model_dump(mode="json")},
        ensure_ascii=False,
        separators=(",", ":"),
    )