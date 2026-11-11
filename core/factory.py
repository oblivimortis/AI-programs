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
from core.context import ContextManager
from core.llm.openai_compat import OpenAICompatChatClient
from core.skills import (
    DEFAULT_SKILLS_DIR,
    SkillLibrary,
    SkillMetadata,
    apply_skill_catalog,
    resolve_skills_roots,
)
from core.tools.builtin.cli_tools import RunCliTool
from core.tools.builtin.context_tools import RecallContextTool
from core.tools.builtin.datetime_tools import GetDateTool, IsLeapYearTool
from core.tools.builtin.filesystem_tools import build_file_system_tools
from core.tools.builtin.skill_tools import CreateSkillTool, LoadSkillTool
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


def _skill_roots(
    config: AppConfig | ToolsConfig,
    project_root: Path,
) -> tuple[Path, ...]:
    """计算需要扫描的技能根目录。"""
    roots: list[Path] = []
    if isinstance(config, AppConfig):
        if not config.skills.enabled:
            return ()
        # 配置中的相对路径沿用 [tools] root，保持与旧配置兼容。
        roots.extend(resolve_skills_roots(config.skills.root, config.tools.root))
    else:
        roots.append(config.root / DEFAULT_SKILLS_DIR)

    # Agent Skills 约定：同时查找程序启动目录下的 .agents/skills。
    roots.append(project_root / DEFAULT_SKILLS_DIR)

    # SkillLibrary 会按绝对路径去重。
    return tuple(roots)


def build_skill_library(
    config: AppConfig | ToolsConfig,
    *,
    project_root: str | Path | None = None,
) -> SkillLibrary:
    """根据配置创建技能库（初始化阶段只读取技能元数据）。"""
    root = Path(project_root or workspace_root()).expanduser().resolve()
    roots = _skill_roots(config, root)
    enabled = bool(roots)
    return SkillLibrary(root, roots, enabled=enabled)


def build_tool_registry(
    config: ToolsConfig,
    search: SearchConfig | None = None,
    skills: SkillLibrary | None = None,
    context: ContextManager | None = None,
) -> ToolRegistry:
    """根据配置装配工具注册表。"""
    resolver = PathResolver(config.root, enforce_root=config.enforce_root)
    registry = ToolRegistry()
    registry.register(GetDateTool())
    registry.register(IsLeapYearTool())
    registry.register(SearchWebTool(search or SearchConfig()))
    registry.register(FetchWebTool())
    registry.register(RunCliTool())

    if context is not None:
        registry.register(RecallContextTool(context))

    library = skills or build_skill_library(config)
    registry.register(LoadSkillTool(library))
    registry.register(CreateSkillTool(library))

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


def load_agent_skills(config: AppConfig) -> list[SkillMetadata]:
    """按配置发现技能元数据；未开启或目录不存在时返回空列表。"""
    if not config.skills.enabled:
        return []
    return list(build_skill_library(config).skills)


def build_agent(
    config: AppConfig,
    system_prompt: str | None = None,
) -> Agent:
    """装配一个可直接使用的 Agent。"""
    project_root = workspace_root()
    library = build_skill_library(config, project_root=project_root)
    client = build_chat_client(config)
    context = ContextManager(
        project_root,
        window_tokens=config.context.window_tokens,
        threshold=config.context.compact_threshold,
    )
    registry = build_tool_registry(
        config.tools, config.search, skills=library, context=context
    )

    base_prompt = system_prompt or config.system_prompt or DEFAULT_SYSTEM_PROMPT
    # 只把 name / description 注入系统提示词，正文由 loadSkill 按需加载。
    prompt = apply_skill_catalog(base_prompt, library.skills)
    prompt_provider = None

    if config.skills.enabled:
        if config.skills.hot_reload:

            def refresh_prompt() -> str:
                library.refresh()
                return apply_skill_catalog(base_prompt, library.skills)

        else:

            def refresh_prompt() -> str:
                # 关闭热重载时不主动扫描文件；createSkill 写入后已经刷新过技能库，
                # 这里只需要把最新清单重新拼进提示词。
                return apply_skill_catalog(base_prompt, library.skills)

        # 每次 run() 前（以及每次工具调用后的新请求前）重新取提示词；
        # 正文仍然只在 loadSkill 时读取。
        prompt_provider = refresh_prompt

    session = ConversationSession(system_prompt=prompt)
    agent = Agent(
        client=client,
        registry=registry,
        session=session,
        max_tool_rounds=config.llm.max_tool_rounds,
        system_prompt=prompt,
        system_prompt_provider=prompt_provider,
        skill_library=library,
        context_manager=context,
    )
    # AGENTS.md：会话首次加载时注入一次，之后随会话快照复用。
    agents_md = project_root / "AGENTS.md"
    if agents_md.is_file():
        agent.inject_agents_md(agents_md)
    return agent


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
    "build_skill_library",
    "build_tool_registry",
    "load_agent_skills",
    "workspace_root",
]
