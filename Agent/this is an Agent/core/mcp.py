"""MCP（Model Context Protocol）客户端与工具适配。

只实现最常用的 stdio 传输：
    * 启动子进程，用 JSON-RPC 2.0 按行通信；
    * ``initialize`` → ``notifications/initialized`` → ``tools/list``；
    * 把 MCP 工具包装成本项目的 ``Tool``，注册后模型就能调用。

加载是异步的：先进入 TUI，再在后台逐个连接服务器，连上后刷新界面。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from core.config.settings import MCPConfig, MCPStdioServer
from core.tools.base import Tool, ToolResult
from core.tools.registry import ToolRegistry


class MCPError(Exception):
    """MCP 通信失败。"""


@dataclass
class MCPServerState:
    """一个 MCP 服务器的当前状态（供侧栏展示）。"""

    name: str
    status: str = "connecting"  # connecting | ready | error
    tools: list[str] = field(default_factory=list)
    error: str = ""


class MCPClient:
    """单个 MCP 服务器的 stdio 客户端。"""

    def __init__(self, server: MCPStdioServer, timeout: float = 15.0) -> None:
        self._server = server
        self._timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._tools: list[dict[str, Any]] = []
        self._state = MCPServerState(name=server.name)

    @property
    def state(self) -> MCPServerState:
        return self._state

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def start(self) -> None:
        """启动进程并完成握手、拉取工具列表。"""
        command = self._server.command
        args = list(self._server.args)
        # Windows 上 .cmd / .bat 需要借助 cmd.exe 启动。
        if command.lower().endswith((".cmd", ".bat")):
            args = ["/c", command, *args]
            command = "cmd"
        self._process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self._server.cwd,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "xagent", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        result = await self._request("tools/list", {})
        self._tools = list(result.get("tools") or [])
        self._state.tools = [
            str(tool.get("name") or "") for tool in self._tools if tool.get("name")
        ]
        self._state.status = "ready"

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用一个 MCP 工具，返回文本结果。"""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        parts: list[str] = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(part for part in parts if part).strip() or "(空结果)"

    async def close(self) -> None:
        """结束子进程。"""
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()

    # ------------------------------------------------------------------
    # JSON-RPC
    # ------------------------------------------------------------------
    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        }
        await self._write(message)
        while True:
            payload = await asyncio.wait_for(self._read(), timeout=self._timeout)
            if payload.get("id") != message["id"]:
                # 服务器主动发来的通知，忽略。
                continue
            if "error" in payload:
                raise MCPError(str(payload.get("error")))
            return payload.get("result") or {}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise MCPError("MCP 进程尚未启动")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise MCPError("MCP 进程尚未启动")
        line = await self._process.stdout.readline()
        if not line:
            raise MCPError("MCP 服务器已关闭连接")
        try:
            return json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP 返回了非 JSON 内容: {exc}") from exc


class MCPTool(Tool):
    """把一个 MCP 工具包装成本项目的 Function Calling 工具。"""

    def __init__(self, client: MCPClient, spec: dict[str, Any]) -> None:
        tool_name = str(spec.get("name") or "")
        self.name = f"mcp__{client.state.name}__{tool_name}"
        self.description = str(spec.get("description") or f"MCP 工具 {tool_name}")
        self._parameters = spec.get("inputSchema") or {
            "type": "object",
            "properties": {},
        }
        self._client = client
        self._remote_name = tool_name

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            content = await self._client.call_tool(
                self._remote_name, dict(arguments)
            )
            return ToolResult.success(content)
        except MCPError as exc:
            return ToolResult.failure(f"MCP 工具调用失败: {exc}")


class MCPManager:
    """管理全部 MCP 服务器，并负责把工具注册进 ToolRegistry。"""

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._clients = [
            MCPClient(server, config.connect_timeout) for server in config.servers
        ]

    @property
    def enabled(self) -> bool:
        return self._config.enabled and bool(self._clients)

    @property
    def clients(self) -> list[MCPClient]:
        return list(self._clients)

    def states(self) -> list[MCPServerState]:
        return [client.state for client in self._clients]

    def register_tools(self, registry: ToolRegistry) -> int:
        """把已连接服务器的工具注册进注册表，返回注册数量。"""
        count = 0
        for client in self._clients:
            if client.state.status != "ready":
                continue
            for spec in client.tools:
                if spec.get("name"):
                    registry.register(MCPTool(client, spec))
                    count += 1
        return count

    async def start_all(
        self,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        """逐个连接服务器；每连上一个就回调一次，便于界面渐进刷新。"""
        if not self.enabled:
            if on_update:
                on_update()
            return
        for client in self._clients:
            try:
                await client.start()
            except Exception as exc:  # noqa: BLE001 - 单个服务器失败不影响其他
                client.state.status = "error"
                client.state.error = str(exc)
            if on_update:
                on_update()
        if on_update:
            on_update()

    async def close_all(self) -> None:
        for client in self._clients:
            await client.close()


__all__ = [
    "MCPClient",
    "MCPError",
    "MCPManager",
    "MCPServerState",
    "MCPTool",
]
