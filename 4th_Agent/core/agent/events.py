"""Agent 事件定义。

core 层通过 **事件流** 与前端通信：Agent 只负责产出事件，不关心事件
如何被渲染。这样 TUI、Web、Desktop 等前端可以各自用最合适的技术消费
同一份事件流（例如 TUI 直接渲染、Web 转成 SSE 推送）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.tools.base import ToolResult


def _to_jsonable(value: Any) -> Any:
    """把事件字段递归转换为可被 ``json.dumps`` 序列化的值。

    事件契约需要跨进程/跨语言传输（Web 前端通常直接转成 JSON 或 SSE），
    因此这里统一处理 dataclass、枚举、Path 等常见类型。
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


class EventType(str, Enum):
    """事件类型枚举。"""

    TURN_STARTED = "turn_started"
    SYSTEM_PROMPT_UPDATED = "system_prompt_updated"
    MESSAGE_DELTA = "message_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    TURN_FINISHED = "turn_finished"
    ERROR = "error"


@dataclass
class AgentEvent:
    """事件基类。"""

    type: EventType = EventType.TURN_STARTED

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化的字典（便于 Web 前端转 JSON / SSE）。"""
        payload: dict[str, Any] = {"type": self.type.value}
        for key, value in self.__dict__.items():
            if key == "type":
                continue
            payload[key] = _to_jsonable(value)
        return payload


@dataclass
class TurnStarted(AgentEvent):
    """一轮问答开始。"""

    type: EventType = EventType.TURN_STARTED
    prompt: str = ""


@dataclass
class SystemPromptUpdated(AgentEvent):
    """系统提示词在回合开始前被热更新（例如技能文件发生变化）。"""

    type: EventType = EventType.SYSTEM_PROMPT_UPDATED


@dataclass
class MessageDelta(AgentEvent):
    """助手回复的流式文本增量。"""

    type: EventType = EventType.MESSAGE_DELTA
    text: str = ""


@dataclass
class ToolCallStarted(AgentEvent):
    """模型发起了一次工具调用。"""

    type: EventType = EventType.TOOL_CALL_STARTED
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCallFinished(AgentEvent):
    """工具调用完成。"""

    type: EventType = EventType.TOOL_CALL_FINISHED
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    result: ToolResult = field(default_factory=lambda: ToolResult(content=""))


@dataclass
class TurnFinished(AgentEvent):
    """一轮问答结束。"""

    type: EventType = EventType.TURN_FINISHED
    reply: str = ""
    rounds: int = 0
    error: str | None = None


@dataclass
class ErrorEvent(AgentEvent):
    """执行过程中发生错误。"""

    type: EventType = EventType.ERROR
    message: str = ""


__all__ = [
    "AgentEvent",
    "ErrorEvent",
    "EventType",
    "MessageDelta",
    "SystemPromptUpdated",
    "ToolCallFinished",
    "ToolCallStarted",
    "TurnFinished",
    "TurnStarted",
]
