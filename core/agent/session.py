"""会话状态：保存一轮对话的完整消息历史。"""

from __future__ import annotations

from typing import Any, Iterable

from core.llm.types import Message, ToolCall


class ConversationSession:
    """对话会话。

    会话只保存 **协议级别** 的消息，不包含任何 UI 状态，
    因此可以在不同前端之间共享或持久化。
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        history: Iterable[Message] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self._messages: list[Message] = list(history or ())
        #: 是否已经注入过项目说明（AGENTS.md），避免重复注入。
        self.agents_md_injected = False

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    @property
    def messages(self) -> list[Message]:
        """当前历史消息副本。"""
        return list(self._messages)

    def to_payload_messages(self) -> list[Message]:
        """生成发送给模型的完整消息列表（含系统提示词）。"""
        payload: list[Message] = []
        if self.system_prompt:
            payload.append(Message(role="system", content=self.system_prompt))
        payload.extend(self._messages)
        return payload

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_user(self, content: str) -> Message:
        message = Message(role="user", content=content)
        self._messages.append(message)
        return message

    def add_assistant(
        self,
        content: str,
        tool_calls: Iterable[ToolCall] | None = None,
    ) -> Message:
        message = Message(role="assistant", content=content, tool_calls=list(tool_calls or ()))
        self._messages.append(message)
        return message

    def add_tool_result(self, tool_call_id: str, content: str, name: str | None = None) -> Message:
        message = Message(role="tool", content=content, tool_call_id=tool_call_id, name=name)
        self._messages.append(message)
        return message

    def replace_messages(self, messages: Iterable[Message]) -> None:
        """用一组消息替换全部历史（保留系统提示词），用于上下文压缩。"""
        self._messages = list(messages)

    def clear(self) -> None:
        """清空历史（保留系统提示词）。"""
        self._messages.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        """导出为可 JSON 序列化的结构。"""
        return [message.to_payload() for message in self._messages]

    def __len__(self) -> int:
        return len(self._messages)


__all__ = ["ConversationSession"]
