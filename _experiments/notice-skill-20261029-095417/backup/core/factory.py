"""组合根（Composition Root）。

所有具体实现的装配集中在这里：
    * 前端只调用 :func:`build_agent` 拿到一个可用的 :class:`~core.agent.runner.Agent`；
    * 前端不需要知道 LLM 用的是哪种协议、文件工具如何实现；
    * 未来新增 Web / Desktop 前端时，复用同一套装配逻辑即可。
"""

from __future__ import annotations

import os
from pathlib import Path

from core.agent.runner import Agent
from core.agent.session import ConversationSession
from core.config.settings import AppConfig, SearchConfig, ToolsConfig, load_config
from core.llm.openai_compat import OpenAICompatChatClient
from core.tools.builtin.cli_tools import RunCliTool
from core.tools.builtin.datetime_tools import GetDateTool, IsLeapYearTool
from core.tools.builtin.filesystem_tools import build_file_system_tools
from core.tools.builtin.web_fetch_tools import FetchWebTool
from core.tools.builtin.web_search_tools import SearchWebTool
from core.tools.paths import PathResolver
from core.tools.registry import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "你是一个运行在终端中的智能助手（Agent）。"
    "你可以调用本地工具来获取时间、查看与修改文件，"
    "也可以使用 searchWeb 搜索互联网获取最新信息，"
    "使用 fetchWeb 读取指定网页的正文，"
    "或使用 RunCli 调用本地已安装的命令行程序。"
    "回答请简洁、结构清晰；涉及文件操作、联网操作或命令执行时先说明将要做什么。"
)


def build_tool_registry(
    config: ToolsConfig,
    search: SearchConfig | None = None,
) -> ToolRegistry:
    """根据配置装配工具注册表。"""
    resolver = PathResolver(config.root, enforce_root=config.enforce_root)
    registry = ToolRegistry()
    registry.register(GetDateTool())
    registry.register(IsLeapYearTool())
    registry.register(SearchWebTool(search or SearchConfig()))
    registry.register(FetchWebTool())
    registry.register(RunCliTool())
    for tool in build_file_system_tools(
        resolver,
        max_read_chars=config.max_read_chars,
        allow_delete=config.allow_delete,
    ):
        registry.register(tool)
    return registry


def build_chat_client(config: AppConfig) -> OpenAICompatChatClient:
    """根据配置装配 LLM 客户端。"""
    return OpenAICompatChatClient(config.llm)


def build_agent(
    config: AppConfig,
    system_prompt: str | None = None,
) -> Agent:
    """装配一个可直接使用的 Agent。"""
    client = build_chat_client(config)
    registry = build_tool_registry(config.tools, config.search)
    prompt = system_prompt or config.system_prompt or DEFAULT_SYSTEM_PROMPT
    session = ConversationSession(system_prompt=prompt)
    return Agent(
        client=client,
        registry=registry,
        session=session,
        max_tool_rounds=config.llm.max_tool_rounds,
        system_prompt=prompt,
    )


def build_default_agent(
    config_path: str | os.PathLike[str] | None = None,
    system_prompt: str | None = None,
) -> tuple[Agent, AppConfig]:
    """加载配置并装配 Agent，同时返回配置供前端展示。"""
    config = load_config(config_path)
    return build_agent(config, system_prompt=system_prompt), config


def workspace_root() -> Path:
    """当前工作区根目录。"""
    return Path.cwd()


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "build_agent",
    "build_chat_client",
    "build_default_agent",
    "build_tool_registry",
    "workspace_root",
]
