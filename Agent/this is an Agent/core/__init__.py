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
    SystemPromptUpdated,
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
    SkillsConfig,
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
from core.factory import (
    build_agent,
    build_default_agent,
    build_skill_library,
    build_tool_registry,
)
from core.llm.base import ChatClient
from core.llm.openai_compat import OpenAICompatChatClient
from core.llm.types import Message, StreamDelta, ToolCall, ToolSpec
from core.skills import (
    Skill,
    SkillCatalog,
    SkillLibrary,
    SkillLoadResult,
    SkillMetadata,
    SkillRuntime,
    apply_skill_catalog,
    apply_skills,
    discover_skill_metadata,
    discover_skills,
    format_skill_catalog,
    resolve_skills_roots,
    split_skill_roots,
)
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
    "SkillRuntime",
    "Skill",
    "SkillCatalog",
    "SkillLibrary",
    "SkillLoadResult",
    "SkillMetadata",
    "PathPolicyError",
    "SearchConfig",
    "SkillsConfig",
    "StreamDelta",
    "SystemPromptUpdated",
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
    "apply_skill_catalog",
    "apply_skills",
    "build_agent",
    "build_default_agent",
    "build_skill_library",
    "build_tool_registry",
    "discover_skill_metadata",
    "discover_skills",
    "format_skill_catalog",
    "load_config",
    "resolve_skills_roots",
    "split_skill_roots",
]
