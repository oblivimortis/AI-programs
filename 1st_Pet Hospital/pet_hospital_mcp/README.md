# Pet Hospital MCP

A stateless Model Context Protocol (MCP) server that exposes the local **Go Pet Hospital REST API** to AI agents. Built with the official Python SDK 2.x (`MCPServer`) and the **MCP 2026-07-28** protocol in **stateless Streamable HTTP** mode. No session storage, no `initialize`/`Mcp-Session-Id`, and no FastMCP usage.

## Key Features

- Protocol: `2026-07-28` (stateless)
- SDK: `mcp==2.0.0` (Python 3.11+), using `MCPServer` (not FastMCP)
- Transport: Stateless Streamable HTTP at `/mcp` (`json_response=True`)
- Endpoint `/health` for liveness + upstream reachability
- Exactly one tool: `list_pets` (adapter over `GET /api/v1/pets`), with strict Pydantic validation
- Unified structured error envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`; failures marked as `isError: true` (SDK 2.x)
- Logs redacted for sensitive fields (`ownerPhone`, `ownerAddr`, `chipNo`, snake_case forms)
- Unit tests with `httpx.MockTransport` only (no real Go service during tests)

## Prerequisites

1. The Go pet hospital REST API must be running and reachable. 
   - Default upstream: `http://127.0.0.1:8080`
   - A convenient prebuilt is included at `../bin/pet-hospital-windows-amd64/windows/pethospital.exe` (from the teaching release). The Go source is in `../backend/` (copied from teaching materials).

2. Python 3.11+.

## Installation

```bash
cd E:\programs1\1st_Pet Hospital\pet_hospital_mcp
# Create venv
python -m venv .venv
# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1
# Install (editable with dev dependencies)
pip install -e ".[dev]"
```

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `MCP_HOST` | `127.0.0.1` | Address the MCP HTTP server listens on. Always localhost-only in this project. |
| `MCP_PORT` | `8002` | Port the MCP HTTP server listens on. (Avoids conflicts with 8000/8001 used elsewhere.) |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | Base URL of the Go pet hospital REST API. Must not have a trailing slash. |
| `PET_HOSPITAL_REQUEST_TIMEOUT` | `10` | Per-request timeout (seconds) to the upstream. |
| `PET_HOSPITAL_RETRY_ATTEMPTS` | `2` | Extra retries for network-level failures (timeout/connect). 4xx/5xx are never retried. |
| `PET_HOSPITAL_RETRY_BACKOFF` | `0.25` | Linear backoff base (seconds). |

## Running

### 1) Start the Go pet hospital backend

Option A (prebuilt, Windows):

```powershell
cd "E:\programs1\1st_Pet Hospital\bin\pet-hospital-windows-amd64\windows"
.\pethospital.exe
# or with seed: .\pethospital.exe -seed
```

By default the Go API listens on `http://127.0.0.1:8080`. Check health: `curl http://127.0.0.1:8080/health`.

Option B (source):

```powershell
cd "E:\programs1\1st_Pet Hospital\backend"
go run .  # or build first
```

### 2) Start the MCP server

```powershell
cd "E:\programs1\1st_Pet Hospital\pet_hospital_mcp"
.\.venv\Scripts\Activate.ps1
python -m pet_hospital_mcp
```

The MCP server listens on `http://127.0.0.1:8002/mcp` (Streamable HTTP, stateless). `/health` is available at `http://127.0.0.1:8002/health`.

## MCP Endpoints

- `GET /health` → service liveness, protocol SDK version, upstream reachability.
- `POST /mcp` → JSON-RPC 2.0 (Streamable HTTP, stateless). Requests must use `Content-Type: application/json`, `Accept: application/json`, and the modern MCP headers as appropriate (`Mcp-Method`, `Mcp-Name` for `tools/*`). Protocol version is `2026-07-28`. No `Mcp-Session-Id` is issued or required.

## Tool: `list_pets`

Name: `list_pets`  
Description: Queries and lists pet hospital records via `GET /api/v1/pets`. Supports filtering, sorting, paging; returns `data.items` with `records/charges` possibly `null` or array.

Parameters (only these; no private adapter params):

`q`, `name`, `ownerName`, `ownerPhone`, `species`, `doctor`, `disease`, `status`, `min`, `max`, `sortBy`, `order`, `page`, `pageSize`

Constraints (strict):
- `species` ∈ {`犬`, `猫`, `兔`, `鸟`, `仓鼠`, `爬宠`, `其他`}  
- `status` ∈ {`待就诊`, `就诊中`, `住院中`, `已康复`, `慢性病随访`}  
- `sortBy` ∈ {`id`, `name`, `ownerName`, `species`, `doctor`, `disease`, `status`, `totalCost`, `visitCount`, `createdAt`, `updatedAt`}  
- `order` ∈ {`asc`, `desc`}  
- `page >= 1` (integer)  
- `1 <= pageSize <= 500` (integer)  
- `min`, `max >= 0` (non-negative float/int)  
- `min <= max`  
- extra fields forbidden; NaN/Infinity and incorrect types rejected

Success response (tool returns JSON string):
```json
{
  "data": {
    "items": [ { "...": "..." } ],
    "total": 1,
    "page": 1,
    "pageSize": 20,
    "totalPages": 1,
    "totalCost": 120.5
  }
}
```

Error response (unified):
```json
{
  "error": {
    "code": "VALIDATION_ERROR|BACKEND_TIMEOUT|BACKEND_UNAVAILABLE|BACKEND_API_ERROR|BACKEND_INVALID_RESPONSE|INTERNAL_ERROR",
    "message": "human-readable",
    "details": { "...": "..." }
  }
}
```
Failures are marked `isError: true` in the MCP tool result (SDK 2.x).

## Verification (MCP Inspector / manual)

### /health
```bash
curl http://127.0.0.1:8002/health
```

### tools/list (stateless discovery)
```bash
python -c "
import httpx
r=httpx.post('http://127.0.0.1:8002/mcp', headers={'Mcp-Method':'tools/list','Accept':'application/json','Content-Type':'application/json'}, json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
print(r.status_code)
print(r.text[:120])
"
```

### tools/call list_pets (success)
```bash
python -c "
import httpx, json
r=httpx.post('http://127.0.0.1:8002/mcp', headers={'Mcp-Method':'tools/call','Mcp-Name':'list_pets','Accept':'application/json','Content-Type':'application/json'}, json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'list_pets','arguments':{'params':{'species':'犬','pageSize':1}}}})
j=r.json(); res=j['result']; assert not res.get('isError')
print('ok')
"
```

### tools/call list_pets (validation error)
```bash
python -c "
import httpx, json
r=httpx.post('http://127.0.0.1:8002/mcp', headers={'Mcp-Method':'tools/call','Mcp-Name':'list_pets','Accept':'application/json','Content-Type':'application/json'}, json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_pets','arguments':{'params':{'species':'恐龙'}}}})
j=r.json(); res=j['result']; assert res.get('isError')
print('error ok')
"
```

## Tests

All tests mock the Go backend (no network access to 8080 during testing).

```powershell
cd "E:\programs1\1st_Pet Hospital\pet_hospital_mcp"
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Expected result: `61 passed` (all tests pass).

Coverage includes: input validation, query param forwarding, timeout/retry/error mapping, output shape compatibility, MCP registration/discovery, stateless flow (no initialize, no `Mcp-Session-Id`), `/health`, logging redaction.

## Implementation Notes

- **Stateless Streamable HTTP**: The app is built via `server.streamable_http_app(stateless_http=True, json_response=True)`. The SDK's hybrid routing means 2026-07-28 requests follow the stateless path; we do not implement session lifecycle (initialize/storage/expiry/max_sessions/resumption). 
- **MCPServer** (SDK 2.x): Tool registered via `@server.tool()`; input is a `params: dict` parameter validated by Pydantic `ListPetsInput`. Tool returns a JSON string.
- **Error semantics**: `McpError` subclasses `ToolError` (SDK) so failures are properly marked `isError=true`. The error text contains the unified JSON payload (fronted by `"Error executing tool ...: "` in the SDK's wrapped message); clients/parsers should treat the trailing JSON object as the structured error envelope when desired.
- **Logging**: JSON-formatted logs include `timestamp, level, logger, message, tool_name, params, status, duration_ms`. Sensitive keys (`ownerPhone`, `ownerAddr`, `chipNo`, snake_case variants) are recursively redacted in `params` and any logged payloads.

## Notes

- Stage 2 tools are NOT implemented in this build (per requirements).
- Do not import or use `mcp.server.fastmcp.FastMCP`.
- Protocol is fixed to `2026-07-28`; SDK fixed to `mcp==2.0.0`.
