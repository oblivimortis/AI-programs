# UPGRADE_PROMPT.md

This prompt preserves the exact specification to extend the project to **Stage 2** (add more MCP tools) while keeping the existing contracts intact. Copy and paste this into your next agent run against this repository.

---

## 目标

在现有 `pet-hospital-mcp/` 基础上，继续完善 **Pet Hospital MCP**，新增若干工具以覆盖 Go 宠物医院 REST API 的常用能力。 

现状（必须严格遵守，不破坏现有行为）：
- 已实现 `list_pets`（对应 `GET /api/v1/pets`），采用 SDK 2.x `MCPServer`、MCP 2026-07-28、无状态 Streamable HTTP（`stateless_http=True, json_response=True`）、统一结构化错误、严格 Pydantic 校验、日志脱敏、MockTransport 单元测试全部通过（当前：`61 passed`）。
- 入口 `python -m pet_hospital_mcp`；`/health` 可用；依赖固定 `mcp==2.0.0`。

## 技术要求（保持不变）

- Python 3.11+
- `mcp==2.0.0`（固定）
- MCP 协议版本 `2026-07-28`
- SDK 2.x 的 `MCPServer`，不得使用或导入 `mcp.server.fastmcp.FastMCP`
- 无状态 Streamable HTTP：不得实现 `initialize`、`Mcp-Session-Id`、会话存储、会话过期、`max_sessions` 或有状态 SSE 恢复机制
- 继续复用现有模块：`src/pet_hospital_mcp/{config,server,rest_client,errors,logging_config}` 和 `tools/` 约定
- 保留 `/health` 端点
- 教学场景不实现认证、权限、CORS 或 Origin 校验
- 上游地址 `PET_HOSPITAL_BASE_URL` 默认 `http://127.0.0.1:8080`
- MCP 默认只监听 `127.0.0.1`，`MCP_HOST`、`MCP_PORT` 可配置
- 统一结构化错误：`{"error":{"code":..., "message":..., "details":{...}}}`，至少保留现有错误码：`VALIDATION_ERROR`、`BACKEND_TIMEOUT`、`BACKEND_UNAVAILABLE`、`BACKEND_API_ERROR`、`BACKEND_INVALID_RESPONSE`、`INTERNAL_ERROR`
- 后端调用必须有超时和有限重试（仅网络级错误重试，不重试 4xx/5xx）
- 不得把 httpx、Pydantic、SDK 或 Python 堆栈原样暴露给 MCP 客户端；按 SDK 2.x 实际规定正确标记工具调用失败状态
- 日志至少包含 `timestamp`、`tool_name`、`params`、`status`、`duration_ms`；`ownerPhone`、`ownerAddr`、`chipNo` 及其 snake_case 写法必须递归脱敏
- 测试使用 `pytest`、`pytest-asyncio`、`httpx.MockTransport`（禁止访问真实 Go 服务）
- `pyproject.toml` 保持现有结构；README 按需更新

## 待新增工具（阶段二建议清单）

按优先级从高到低，新增以下工具（每个工具都必须是严格适配 Go REST API 的 MCP 工具）：

| 工具名 | 对应 Go 接口 | 说明 |
|---|---|---|
| `get_pet` | `GET /api/v1/pets/{id}` | 获取单个宠物详情（含 `records`、`charges` 结构）。输入需校验 `id` 非空。 |
| `add_pet_record` | `POST /api/v1/pets/{id}/records` | 新增就诊记录。需严格校验必填字段、类型、范围。 |
| `add_pet_charge` | `POST /api/v1/pets/{id}/charges` | 新增收费明细。需严格校验必填字段、类型、范围。 |

> 上表仅作参考。实际新增前，必须先**查阅 Go 后端的真实 `internal/api` 路由与数据模型**（尤其是请求体 Schema、返回 Envelope），再决定最终工具集与参数。不得臆造字段。

## 每个工具的开发规范（强制）

1. **仅适配真实后端**  
   - 每个工具只暴露 Go API 实际存在的查询参数/路径参数/请求体字段。  
   - 不新增适配器私有业务参数。

2. **严格校验**  
   - 使用 Pydantic 定义**输入**、**成功输出**、**错误输出**（沿用现有风格）。  
   - `extra="forbid"`、`strict=True`（视字段类型合理选用），拒绝未知字段、NaN、Infinity、类型错误。  
   - 枚举值必须直接来源于真实后端允许值。  
   - 数值范围、最小长度、路径参数非空等一律校验。  
   - `records`、`charges` 等可能为 `null` 或数组的字段，输出模型必须兼容。

3. **工具实现**  
   - 新增 `src/pet_hospital_mcp/tools/<tool_name>.py`，导出 `handle(client: RestClient, raw_args: dict[str,Any]|None)` 函数（或等效 async 函数），返回**JSON 字符串**（成功形如 `{"data": {...}}`；失败抛出 `McpError`）。  
   - 在 `src/pet_hospital_mcp/tools/__init__.py` 统一导出。  
   - 在 `src/pet_hospital_mcp/server.py` 中用 `@server.tool()` 注册新工具，注册名用 `snake_case`，工具描述必须包含用途、参数、适用场景、返回值。  
   - 对于 `POST` 类接口，`RestClient` 需新增 `post_json` 方法（复用超时、有限重试、错误映射逻辑；4xx/5xx 不重试）。对于 `GET` 单条，可复用 `get_json`。

4. **错误处理（完全一致）**  
   - 全部异常映射到统一结构化错误。  
   - 后端 4xx/5xx → `BACKEND_API_ERROR`，并尽量携带后端 `message/code`。  
   - 超时 → `BACKEND_TIMEOUT`，连接异常 → `BACKEND_UNAVAILABLE`。  
   - 后端返回非 JSON、Envelope 缺少 `data` 或类型不符 → `BACKEND_INVALID_RESPONSE`。  
   - 输入校验失败 → `VALIDATION_ERROR`。  
   - 其他意外 → `INTERNAL_ERROR`，且不泄露堆栈。

5. **日志脱敏**  
   - 所有工具调用都必须调用 `log_tool_call`，参数传入原始校验前的 `params` 字典（已由 `redact` 处理敏感字段）。  
   - 新增工具涉及敏感字段同样覆盖（`ownerPhone`、`ownerAddr`、`chipNo` 及 snake_case）。

6. **测试（必须全覆盖，MockTransport）**  
   - 为每个新工具新增单元测试文件 `tests/test_<tool_name>.py`，至少覆盖：正常转发（路径/参数/请求体）、输入校验失败、4xx/5xx、超时/连接异常、后端非法 JSON/Envelope 不符。  
   - 在 `tests/test_server.py` 新增/补充 HTTP 端到端用例：`tools/list` 能发现新工具、`tools/call <tool>` 成功、`tools/call <tool>` 校验失败、继续验证**无状态**（不发送 `initialize`，不要求/返回 `Mcp-Session-Id`）、`/health` 正常。  
   - **禁止**访问真实 Go 服务。  
   - 最终必须 `cd pet_hospital_mcp && python -m pytest -q` 全部通过（新增后总数>=61）。

## 开始指令

1. 先研读 Go 后端真实接口（`../backend/internal/api/*.go`、`../backend/internal/model/*.go`、`../backend/README*`）以获取真实路径、参数、枚举、返回 Envelope、错误格式。
2. 根据研读结果制定工具清单并列出变更文件。
3. 先实现 `RestClient` 扩展（如需 `POST`），再逐个工具模块+注册+测试。
4. 全量测试通过后，更新 README 与本文件交付声明。
5. 输出：目录结构、修改文件列表、测试命令与真实结果、启动顺序、验证无状态调用示例、最终声明。

**最终声明：本次交付已完成 stage 1（仅 `list_pets`），未实现 stage 2 工具。**