"""MCP server assembly.

Builds an SDK 2.x ``MCPServer`` (not FastMCP) hosting the single ``list_pets``
tool, plus a ``/health`` endpoint via ``custom_route``. The HTTP app is served
in the stateless Streamable HTTP mode (``stateless_http=True``): there is no
``initialize`` handshake, no ``Mcp-Session-Id``, no session storage or expiry,
and no SSE resumption — exactly the 2026-07-28 stateless model.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import McpError, internal_error
from pet_hospital_mcp.logging_config import log_tool_call
from pet_hospital_mcp.rest_client import RestClient
from pet_hospital_mcp.tools import list_pets as list_pets_tool

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2026-07-28"

TOOL_DESCRIPTION = (
    "list_pets: 查询并列出宠物医院档案，严格对应 Go REST API 的 GET /api/v1/pets。"
    "用途：按关键词/姓名/主人/种类/医生/疾病/状态/费用区间筛选，支持排序与分页；"
    "适用场景：档案检索、统计与筛选导出前预览。"
    "参数：仅支持 q、name、ownerName、ownerPhone、species、doctor、disease、status、"
    "min、max、sortBy、order、page、pageSize（与后端一一对应，无私有参数）。"
    "返回值：JSON 对象 {\"data\": {items, total, page, pageSize, totalPages, totalCost}}，"
    "其中 items 内每条档案含 records/charges（可能为 null 或数组）。"
)


def build_server(settings: Settings, client: RestClient | None = None) -> MCPServer:
    """Create the MCPServer with its single tool and the /health route."""
    rest_client = client or RestClient(
        base_url=settings.pet_hospital_base_url,
        timeout=settings.request_timeout,
        retry_attempts=settings.retry_attempts,
        retry_backoff=settings.retry_backoff,
    )

    server = MCPServer(
        name="pet-hospital-mcp",
        title="Pet Hospital MCP",
        version="0.1.0",
        instructions=(
            "Stateless MCP server (protocol 2026-07-28) exposing the local Go "
            "pet hospital REST API to AI agents. The only business backend is "
            "the Go service and it is never modified. Currently provides "
            "exactly one tool: list_pets. Stage-2 tools are NOT implemented."
        ),
    )

    @server.tool()
    async def list_pets(params: dict) -> str:
        """Query pet records from the local pet hospital backend.

        Args:
            params: Filter / sort / pagination options. Keys must be a subset of
                {q, name, ownerName, ownerPhone, species, doctor, disease,
                status, min, max, sortBy, order, page, pageSize}. species ∈
                {犬,猫,兔,鸟,仓鼠,爬宠,其他}; status ∈ {待就诊,就诊中,住院中,
                已康复,慢性病随访}; sortBy ∈ {id,name,ownerName,species,doctor,
                disease,status,totalCost,visitCount,createdAt,updatedAt};
                order ∈ {asc,desc}; page ≥ 1; 1 ≤ pageSize ≤ 500; min/max ≥ 0
                and min ≤ max.

        Returns:
            JSON string ``{"data": {"items": [...], "total", "page",
            "pageSize", "totalPages", "totalCost"}}``.
        """
        started = time.perf_counter()
        try:
            output = await list_pets_tool.handle(rest_client, params)
        except McpError:
            log_tool_call(logger, "list_pets", params, "error", _ms(started))
            raise
        except Exception as exc:  # never leak internals to the client
            logger.exception("list_pets crashed unexpectedly")
            log_tool_call(logger, "list_pets", params, "error", _ms(started))
            raise internal_error(
                f"Unexpected {type(exc).__name__} while running list_pets"
            ) from exc
        log_tool_call(logger, "list_pets", params, "ok", _ms(started))
        return output

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Health check: reports MCP service status and upstream reachability."""
        upstream = await rest_client.ping()
        return JSONResponse(
            {
                "status": "ok",
                "service": "pet-hospital-mcp",
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "sdk": "mcp==2.0.0 (MCPServer)",
                "upstream": "reachable" if upstream else "unreachable",
                "upstreamBaseUrl": settings.pet_hospital_base_url,
            }
        )

    return server


def create_app(settings: Settings | None = None, client: RestClient | None = None):
    """Build the ASGI app in stateless Streamable HTTP mode.

    ``stateless_http=True`` disables sessions entirely (no initialize /
    Mcp-Session-Id / session storage / expiry / SSE resumption).
    ``json_response=True`` answers each POST with a single JSON document.
    """
    cfg = settings or Settings.from_env()
    server = build_server(cfg, client)
    return server.streamable_http_app(stateless_http=True, json_response=True)


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0