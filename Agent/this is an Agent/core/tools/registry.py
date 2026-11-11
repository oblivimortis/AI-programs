"""工具注册表：负责工具的注册、查询与调度执行。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from core.llm.types import ToolSpec
from core.tools.base import Tool, ToolResult


class ToolRegistry:
    """工具集合的容器。

    面向模型的 schema 列表由注册表统一生成，新增工具只需 ``register`` 一次，
    无需修改任何协议相关代码。
    """

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        """注册一个工具（同名工具会被覆盖）。"""
        if not tool.name:
            raise ValueError("工具必须提供非空的 name")
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def specs(self) -> list[ToolSpec]:
        """生成发送给模型的工具 schema 列表。"""
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """按名称执行工具。

        未知工具不会抛出异常，而是返回失败结果 —— 失败信息回传给模型后，
        模型有机会自行纠正。
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                f"unknown tool: {name}",
                available_tools=self.names(),
            )
        try:
            return await tool.execute(arguments or {})
        except Exception as exc:  # noqa: BLE001 - 工具异常需要回传给模型
            return ToolResult.failure(f"工具 {name} 执行失败: {exc}", tool=name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


__all__ = ["ToolRegistry"]
