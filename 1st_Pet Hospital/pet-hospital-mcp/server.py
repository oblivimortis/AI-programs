"""
Pet Hospital MCP Server
将 Go 宠物医院 REST API 的 GET /api/v1/pets 能力暴露为 MCP 工具 list_pets。

运行:
    python server.py
默认监听 http://127.0.0.1:3100/mcp ，通过 streamable-http 传输，
自动兼容新旧 MCP 协议：
  - 旧版(2025-*): initialize 握手 + Mcp-Session-Id 会话
  - 新版(2026-07-28): 单次 POST，无握手无会话
"""
import os
import json
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

# 宠物医院 REST API 地址
PET_API = os.environ.get("PET_HOSPITAL_API", "http://127.0.0.1:8080/api/v1/pets")

server = MCPServer(
    name="pet-hospital-mcp",
    title="宠物医院 MCP 服务",
    description="查询宠物医院档案列表（过滤+排序+分页）",
    version="0.1.0",
)


def _fetch_pets(params: dict) -> dict:
    """调用 GET /api/v1/pets，返回原始响应字典。"""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = PET_API + ("?" + query if query else "")
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


@server.tool(
    name="list_pets",
    description="查询宠物医院档案列表，支持过滤、排序和分页。对应 GET /api/v1/pets。",
)
async def list_pets(
    keyword: str | None = None,
    name: str | None = None,
    species: str | None = None,
    breed: str | None = None,
    gender: str | None = None,
    doctor: str | None = None,
    disease: str | None = None,
    status: str | None = None,
    owner_name: str | None = None,
    owner_phone: str | None = None,
    chip_no: str | None = None,
    min_cost: float | None = None,
    max_cost: float | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[TextContent]:
    """
    查询宠物档案列表。

    Args:
        keyword: 关键词搜索（跨字段，对应 API 的 q 参数）
        name: 宠物姓名
        species: 种类（犬/猫/兔/鸟/仓鼠/爬宠/其他）
        breed: 品种
        gender: 性别（公/母）
        doctor: 主治医生
        disease: 疾病
        status: 就诊状态（待就诊/就诊中/住院中/已康复/慢性病随访）
        owner_name: 主人姓名
        owner_phone: 主人电话
        chip_no: 芯片号
        min_cost: 总花费下限
        max_cost: 总花费上限
        sort_by: 排序字段（id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt）
        sort_order: 排序方向（asc/desc）
        page: 页码（默认 1）
        page_size: 每页条数（默认 20）
    """
    # 映射 MCP 参数到 Go API 查询参数
    api_params = {
        "q": keyword,
        "name": name,
        "species": species,
        "breed": breed,
        "gender": gender,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "chipNo": chip_no,
        "min": min_cost,
        "max": max_cost,
        "sortBy": sort_by,
        "order": sort_order,
        "page": page,
        "pageSize": page_size,
    }

    result = _fetch_pets(api_params)
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


def main():
    port = int(os.environ.get("MCP_PORT", "3100"))
    print(f"Pet Hospital MCP server starting on http://127.0.0.1:{port}/mcp")
    print(f"Backend API: {PET_API}")
    server.run(transport="streamable-http", host="127.0.0.1", port=port, stateless_http=True)


if __name__ == "__main__":
    main()
