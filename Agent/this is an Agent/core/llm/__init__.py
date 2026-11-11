"""大模型接入层：端口 + 适配器。"""

from __future__ import annotations

from core.llm.base import ChatClient
from core.llm.openai_compat import OpenAICompatChatClient
from core.llm.sse import aiter_sse_payloads, iter_sse_payloads
from core.llm.types import (
    Message,
    StreamDelta,
    ToolCall,
    ToolCallAccumulator,
    ToolCallDelta,
    ToolSpec,
)

__all__ = [
    "ChatClient",
    "Message",
    "OpenAICompatChatClient",
    "StreamDelta",
    "ToolCall",
    "ToolCallAccumulator",
    "ToolCallDelta",
    "ToolSpec",
    "aiter_sse_payloads",
    "iter_sse_payloads",
]
