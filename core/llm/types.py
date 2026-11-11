"""LLM 领域模型：消息、工具定义、流式增量。

这些结构是 core 层与所有前端（TUI / Web / Desktop / Mobile）之间共享的
"传输协议"，前端只依赖这里的数据结构，不依赖任何具体 SDK。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """一次完整的工具调用。"""

    id: str
    name: str
    #: 原始 JSON 字符串（OpenAI 兼容协议中 arguments 以字符串传递）。
    arguments: str = ""

    def parsed_arguments(self) -> dict[str, Any]:
        """把 arguments 解析为字典，解析失败时返回空字典。"""
        raw = (self.arguments or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def to_payload(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容协议的 tool_call 结构。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass
class Message:
    """一条对话消息。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容协议的消息结构。"""
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.to_payload() for call in self.tool_calls]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(frozen=True)
class ToolSpec:
    """工具的 JSON Schema 描述，随请求发送给模型。"""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCallDelta:
    """流式响应中的工具调用增量分片。"""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamDelta:
    """一次流式响应增量。"""

    content: str = ""
    tool_calls: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.content and not self.tool_calls and not self.finish_reason


class ToolCallAccumulator:
    """把流式的工具调用分片聚合成完整的 :class:`ToolCall`。

    OpenAI 兼容协议中，工具调用的 name / arguments 会分多个 chunk 返回，
    需要按 ``index`` 归并。
    """

    def __init__(self) -> None:
        self._calls: dict[int, ToolCall] = {}

    def add(self, delta: ToolCallDelta) -> None:
        call = self._calls.setdefault(delta.index, ToolCall(id="", name="", arguments=""))
        if delta.id:
            call.id = delta.id
        if delta.name:
            call.name += delta.name
        if delta.arguments:
            call.arguments += delta.arguments

    def extend(self, deltas: Sequence[ToolCallDelta]) -> None:
        for delta in deltas:
            self.add(delta)

    def build(self) -> list[ToolCall]:
        """返回按 index 排序的完整工具调用列表。"""
        return [self._calls[index] for index in sorted(self._calls)]

    def clear(self) -> None:
        self._calls.clear()

    def __len__(self) -> int:
        return len(self._calls)


__all__ = [
    "Message",
    "Role",
    "StreamDelta",
    "ToolCall",
    "ToolCallAccumulator",
    "ToolCallDelta",
    "ToolSpec",
]
