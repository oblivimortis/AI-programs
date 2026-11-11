"""LLM 端口（Port）定义。

core 层通过本模块定义的协议依赖"大模型客户端"，具体实现（OpenAI 兼容
HTTP、SDK、本地模型……）属于适配器，放在 ``core/llm`` 的实现模块中。
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, Sequence, runtime_checkable

from core.llm.types import Message, StreamDelta, ToolSpec


@runtime_checkable
class ChatClient(Protocol):
    """流式对话客户端协议。"""

    @property
    def model(self) -> str:
        """当前使用的模型名。"""
        ...

    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[StreamDelta]:
        """发送一次对话请求，逐段产出流式增量。

        Args:
            messages: 已按协议格式化的消息列表。
            tools: 本轮允许模型调用的工具 schema。

        Yields:
            StreamDelta: 文本增量 / 工具调用增量。
        """
        ...


__all__ = ["ChatClient"]
