"""工具（Tool）抽象与执行结果。

工具是 core 层向模型暴露能力的唯一方式。工具实现应当是 **无 UI 依赖** 的
纯业务逻辑，便于被 TUI / Web / Desktop 等任意前端复用，也便于单元测试。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

from core.llm.types import ToolSpec


@dataclass
class ToolResult:
    """工具执行结果。"""

    #: 回传给模型的文本内容。
    content: str
    #: 是否执行成功。
    ok: bool = True
    #: 结构化附加信息（供前端展示，不回传给模型）。
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: str, **metadata: Any) -> "ToolResult":
        return cls(content=content, ok=True, metadata=metadata)

    @classmethod
    def failure(cls, content: str, **metadata: Any) -> "ToolResult":
        return cls(content=content, ok=False, metadata=metadata)


class Tool(ABC):
    """所有工具的基类。

    子类通过类属性声明元信息，通过 :meth:`parameters` 声明 JSON Schema，
    通过 :meth:`execute` 实现具体业务逻辑。
    """

    #: 工具名（模型调用时使用，必须与 schema 中的 name 一致）。
    name: ClassVar[str] = ""
    #: 面向模型的工具描述。
    description: ClassVar[str] = ""

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        """工具参数的 JSON Schema。"""
        return {"type": "object", "properties": {}}

    @classmethod
    def spec(cls) -> ToolSpec:
        """生成发送给模型的工具描述。"""
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            parameters=cls.parameters(),
        )

    @abstractmethod
    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        """执行工具。

        Args:
            arguments: 模型给出的参数（已解析为字典）。

        Returns:
            ToolResult: 执行结果，``content`` 会被回传给模型。
        """
        raise NotImplementedError


__all__ = ["Tool", "ToolResult"]
