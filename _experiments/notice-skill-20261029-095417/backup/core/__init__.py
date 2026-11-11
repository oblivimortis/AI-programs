"""core 层：与界面无关的 Agent 内核。

分层约定：
    * ``core.config``  —— 配置
    * ``core.llm``      —— 大模型端口与适配器
    * ``core.tools``    —— 工具（能力）定义与注册
    * ``core.agent``    —— 编排器、会话、事件
    * ``core.factory``  —— 组合根

core 层 **不允许** import 任何前端框架（TUI / Web / Desktop）。
"""

from __future__ import annotations

from core.agent.events import (
    AgentEvent,
    ErrorEvent,
    EventType,
    MessageDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.config.settings import (
    AppConfig,
    LLMConfig,
    SearchConfig,
    ToolsConfig,
    load_config,
)
from core.errors import (
    AgentError,
    ConfigurationError,
    LLMError,
    PathPolicyError,
    ToolError,
    ToolNotFoundError,
)
from core.factory import build_agent, build_default_agent, build_tool_registry
from core.llm.base import ChatClient
from core.llm.openai_compat import OpenAICompatChatClient
from core.llm.types import Message, StreamDelta, ToolCall, ToolSpec
from core.tools.base import Tool, ToolResult
from core.tools.registry import ToolRegistry

__all__ = [
    "Agent",
    "AgentError",
    "AgentEvent",
    "AppConfig",
    "ChatClient",
    "ConfigurationError",
    "ConversationSession",
    "ErrorEvent",
    "EventType",
    "LLMConfig",
    "LLMError",
    "Message",
    "MessageDelta",
    "OpenAICompatChatClient",
    "PathPolicyError",
    "SearchConfig",
    "StreamDelta",
    "Tool",
    "ToolCall",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolsConfig",
    "TurnFinished",
    "TurnStarted",
    "build_agent",
    "build_default_agent",
    "build_tool_registry",
    "load_config",
]
