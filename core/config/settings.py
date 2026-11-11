"""配置层：把 ``config.ini`` / 环境变量转换为强类型的配置对象。

设计要点：
    * 配置对象是 **不可变** 的（frozen dataclass），避免运行期被意外篡改；
    * 优先级：显式参数 > 环境变量 > ``config.ini`` > 内置默认值；
    * 该模块不依赖任何 UI 框架，可被任意前端复用。
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.errors import ConfigurationError
from core.skills import split_skill_roots

#: 环境变量前缀，例如 ``AGENT_LLM_API_KEY``。
ENV_PREFIX = "AGENT_"

#: 默认配置文件名。
DEFAULT_CONFIG_NAME = "config.ini"

#: 百度千帆智能搜索生成默认使用的模型。
DEFAULT_SEARCH_MODEL = "ernie-4.5-turbo-32k"


@dataclass(frozen=True)
class LLMConfig:
    """大模型接入参数。"""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    #: 单次提问最多允许的工具调用轮次（与原脚本的 ``range(5)`` 语义一致）。
    max_tool_rounds: int = 5
    #: 单次 HTTP 请求超时时间（秒）。
    request_timeout: float = 120.0
    #: 是否在请求中携带 tools schema。
    enable_tools: bool = True

    def auth_headers(self) -> dict[str, str]:
        """构造鉴权请求头。"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


@dataclass(frozen=True)
class ToolsConfig:
    """工具（尤其是文件类工具）的运行参数。"""

    #: 工作区根目录，相对路径以此为基准。
    root: Path
    #: 是否强制所有路径都位于 ``root`` 之内（沙箱模式）。
    enforce_root: bool = False
    #: 是否允许删除类工具（removeFile / removeDir）。
    allow_delete: bool = True
    #: 读取文件时的最大字符数，超出部分截断。
    max_read_chars: int = 50_000


@dataclass(frozen=True)
class SkillsConfig:
    """技能（Skills）加载参数。

    技能文件位于 ``<root>/<skill_name>/SKILL.md``（或直接 ``<root>/SKILL.md``）。
    初始化时只读取 Front Matter 中的 name / description，完整正文由
    ``loadSkill`` 工具按需加载。``root`` 支持多个目录：配置文件中用逗号、
    分号或换行分隔，相对路径统一以 ``ToolsConfig.root`` 为基准。
    默认开启；需要完全关闭时可显式设为 ``false``。
    """

    #: 是否启用技能发现与 loadSkill。
    enabled: bool = True
    #: 技能根目录（可多个）；相对路径以 ``ToolsConfig.root`` 为基准。
    root: tuple[Path, ...] = (Path(".agents/skills"),)
    #: 是否在每轮对话前检查 SKILL.md 变化并热重载系统提示词。
    hot_reload: bool = True

    def __post_init__(self) -> None:
        # 兼容直接传 Path / 字符串（含逗号分隔）的旧写法。
        normalized = split_skill_roots(self.root)
        if normalized != self.root:
            object.__setattr__(self, "root", normalized)


@dataclass(frozen=True)
class SearchConfig:
    """网络搜索（百度千帆智能搜索生成）参数。"""

    #: 百度千帆 API Key，空字符串表示未配置。
    baidu_qianfan_api_key: str = ""
    #: 智能搜索生成使用的模型；不传模型时 API 只会执行普通搜索，不生成摘要。
    model: str = DEFAULT_SEARCH_MODEL
    #: 单次搜索请求超时时间（秒）。智能搜索生成包含模型总结，通常比普通 HTTP 慢。
    request_timeout: float = 60.0


@dataclass(frozen=True)
class ContextConfig:
    """上下文窗口与压缩参数。"""

    #: 上下文窗口大小（Token）。无法从模型 API 获取时使用这个值。
    window_tokens: int = 128_000
    #: 达到该比例时警告并暂停普通提问。
    compact_threshold: float = 0.70


@dataclass(frozen=True)
class MCPStdioServer:
    """一个通过 stdio 连接的 MCP 服务器。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None


@dataclass(frozen=True)
class MCPConfig:
    """MCP（Model Context Protocol）服务器配置。"""

    enabled: bool = False
    servers: tuple[MCPStdioServer, ...] = ()
    #: 连接与工具列表请求的超时时间（秒）。
    connect_timeout: float = 15.0


def parse_mcp_servers(value: str) -> tuple[MCPStdioServer, ...]:
    """解析 ``name|command|arg1;arg2, name2|command2`` 形式的 MCP 配置。"""
    servers: list[MCPStdioServer] = []
    for chunk in (value or "").split(","):
        parts = [part.strip() for part in chunk.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        args = (
            tuple(arg.strip() for arg in parts[2].split(";") if arg.strip())
            if len(parts) > 2
            else ()
        )
        servers.append(MCPStdioServer(name=parts[0], command=parts[1], args=args))
    return tuple(servers)


#: 没有 config.ini 时写入的示例配置（全部为 mock 值）。
MOCK_CONFIG_TEMPLATE = """\
# 首次启动自动生成的配置。请填写真实的模型信息后重新启动。
[llm]
base_url = https://api.example.com/v1/chat/completions
api_key = sk-mock-replace-me
model = mock-model
temperature = 0.7
max_tool_rounds = 5

[tools]
root = .
enforce_root = false
allow_delete = true
max_read_chars = 50000

[skills]
enabled = true
hot_reload = true

[context]
# 模型上下文窗口（Token）；未知时按 128000 计算。
window = 128000
# 达到该比例时提示并暂停普通提问。
compact_threshold = 0.70

[mcp]
# 是否启用 MCP 服务器（需要先配置 servers）。
enabled = false
# 格式：名称|命令|参数1;参数2, 名称2|命令2
# 示例：filesystem|npx|-y;@modelcontextprotocol/server-filesystem;./
servers =
connect_timeout = 15
"""


def write_mock_config(path: str | Path) -> Path:
    """写入一份带 mock 信息的 config.ini，返回路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(MOCK_CONFIG_TEMPLATE, encoding="utf-8")
    return target


@dataclass(frozen=True)
class AppConfig:
    """应用级配置聚合根。"""

    llm: LLMConfig
    tools: ToolsConfig
    #: 网络搜索配置。
    search: SearchConfig = field(default_factory=SearchConfig)
    #: 技能加载配置。
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    #: 上下文窗口与压缩参数。
    context: ContextConfig = field(default_factory=ContextConfig)
    #: MCP 服务器配置。
    mcp: MCPConfig = field(default_factory=MCPConfig)
    #: 系统提示词，注入到每轮对话的最前面。
    system_prompt: str | None = None
    #: 配置文件来源路径，便于前端展示与排错。
    source_path: Path | None = None
    #: 额外扩展点：预留给未来前端/插件使用的自由配置。
    extras: dict[str, Any] = field(default_factory=dict)


def _env(name: str) -> str | None:
    """读取环境变量（去掉统一前缀）。"""
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    if value is None:
        return None
    value = value.strip()
    return value or None


def _to_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def find_config_file(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """按优先级查找配置文件：

    1. 显式传入的路径；
    2. 环境变量 ``AGENT_CONFIG``；
    3. 当前工作目录下的 ``config.ini``；
    4. 从当前目录向上逐级查找 ``config.ini``（便于在 IDE 中直接运行）。
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"指定的配置文件不存在: {path}")
        return path.resolve()

    env_path = _env("CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"环境变量 AGENT_CONFIG 指向的文件不存在: {path}")
        return path.resolve()

    candidates = [Path.cwd() / DEFAULT_CONFIG_NAME]
    candidates.extend(parent / DEFAULT_CONFIG_NAME for parent in Path.cwd().parents)
    # 打包成 exe 后，配置文件通常和可执行文件放在同一个目录。
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / DEFAULT_CONFIG_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_config(explicit: str | os.PathLike[str] | None = None) -> AppConfig:
    """加载应用配置。

    Raises:
        ConfigurationError: 配置缺失必需项时抛出。
    """
    config_path = find_config_file(explicit)

    parser = configparser.ConfigParser()
    if config_path is not None:
        parser.read(config_path, encoding="utf-8")

    llm_section = parser["llm"] if parser.has_section("llm") else {}

    base_url = _env("LLM_BASE_URL") or llm_section.get("base_url", "").strip()
    api_key = _env("LLM_API_KEY") or llm_section.get("api_key", "").strip()
    model = _env("LLM_MODEL") or llm_section.get("model", "").strip()

    missing = [
        name
        for name, value in (("base_url", base_url), ("api_key", api_key), ("model", model))
        if not value
    ]
    if missing:
        location = config_path if config_path else "(未找到 config.ini)"
        raise ConfigurationError(
            f"配置缺少必需项 {missing}，来源: {location}。"
            " 请复制 config.ini.example 为 config.ini 并填写，或使用环境变量 "
            "AGENT_LLM_BASE_URL / AGENT_LLM_API_KEY / AGENT_LLM_MODEL。"
        )

    temperature = _to_float(
        _env("LLM_TEMPERATURE") or llm_section.get("temperature", "0.7"), 0.7
    )
    max_tool_rounds = _to_int(
        _env("LLM_MAX_TOOL_ROUNDS") or llm_section.get("max_tool_rounds", "5"), 5
    )
    request_timeout = _to_float(
        _env("LLM_TIMEOUT") or llm_section.get("request_timeout", "120"), 120.0
    )
    max_tokens_raw = _env("LLM_MAX_TOKENS") or llm_section.get("max_tokens", "")
    max_tokens = _to_int(max_tokens_raw, 0) if max_tokens_raw else None
    enable_tools = _to_bool(
        _env("LLM_ENABLE_TOOLS") or llm_section.get("enable_tools", "true"), True
    )

    llm = LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens or None,
        max_tool_rounds=max_tool_rounds,
        request_timeout=request_timeout,
        enable_tools=enable_tools,
    )

    tools_section = parser["tools"] if parser.has_section("tools") else {}
    root_raw = _env("TOOLS_ROOT") or tools_section.get("root", "").strip()
    root = Path(root_raw).expanduser() if root_raw else Path.cwd()
    tools = ToolsConfig(
        root=root.resolve(),
        enforce_root=_to_bool(
            _env("TOOLS_ENFORCE_ROOT") or tools_section.get("enforce_root", "false"), False
        ),
        allow_delete=_to_bool(
            _env("TOOLS_ALLOW_DELETE") or tools_section.get("allow_delete", "true"), True
        ),
        max_read_chars=_to_int(
            _env("TOOLS_MAX_READ_CHARS") or tools_section.get("max_read_chars", "50000"),
            50_000,
        ),
    )

    skills_section = parser["skills"] if parser.has_section("skills") else {}
    skills_root_raw = (
        _env("SKILLS_ROOT") or skills_section.get("root", "").strip()
    )
    skills = SkillsConfig(
        enabled=_to_bool(
            _env("SKILLS_ENABLED") or skills_section.get("enabled", "true"), True
        ),
        # 留空时沿用默认根目录；支持 `a, b` / `a; b` 等多目录写法。
        root=split_skill_roots(skills_root_raw) if skills_root_raw else SkillsConfig().root,
        hot_reload=_to_bool(
            _env("SKILLS_HOT_RELOAD") or skills_section.get("hot_reload", "true"), True
        ),
    )

    search_section = parser["search"] if parser.has_section("search") else {}
    # 兼容旧格式：某些 config.ini 把密钥写成 `search = <key>`（可能位于 [search]
    # 或 [agent] 小节中），而不是规范的 baidu_qianfan_api_key 选项。
    legacy_search_key = (
        (search_section.get("search", "") or "").strip()
        or (
            (parser["agent"].get("search", "") or "").strip()
            if parser.has_section("agent")
            else ""
        )
        or (parser.defaults().get("search") or "").strip()
    )
    search_api_key = (
        _env("SEARCH_BAIDU_QIANFAN_API_KEY")
        or _env("BAIDU_QIANFAN_API_KEY")
        or search_section.get("baidu_qianfan_api_key", "").strip()
        or legacy_search_key
    )
    search_model = (
        _env("SEARCH_MODEL")
        or search_section.get("model", "").strip()
        or DEFAULT_SEARCH_MODEL
    )
    search = SearchConfig(
        baidu_qianfan_api_key=search_api_key,
        model=search_model,
        request_timeout=_to_float(
            _env("SEARCH_TIMEOUT") or search_section.get("request_timeout", "60"), 60.0
        ),
    )

    system_prompt = _env("SYSTEM_PROMPT")
    if system_prompt is None and parser.has_section("agent"):
        system_prompt = parser["agent"].get("system_prompt", "").strip() or None

    context_section = parser["context"] if parser.has_section("context") else {}
    context = ContextConfig(
        window_tokens=_to_int(
            _env("CONTEXT_WINDOW") or context_section.get("window", "128000"), 128_000
        ),
        compact_threshold=_to_float(
            _env("CONTEXT_THRESHOLD")
            or context_section.get("compact_threshold", "0.70"),
            0.70,
        ),
    )

    mcp_section = parser["mcp"] if parser.has_section("mcp") else {}
    mcp = MCPConfig(
        enabled=_to_bool(
            _env("MCP_ENABLED") or mcp_section.get("enabled", "false"), False
        ),
        servers=parse_mcp_servers(
            _env("MCP_SERVERS") or mcp_section.get("servers", "")
        ),
        connect_timeout=_to_float(
            _env("MCP_TIMEOUT") or mcp_section.get("connect_timeout", "15"), 15.0
        ),
    )

    return AppConfig(
        llm=llm,
        tools=tools,
        search=search,
        skills=skills,
        context=context,
        mcp=mcp,
        system_prompt=system_prompt,
        source_path=config_path,
    )


__all__ = [
    "AppConfig",
    "ContextConfig",
    "LLMConfig",
    "MCPConfig",
    "MCPStdioServer",
    "MOCK_CONFIG_TEMPLATE",
    "SearchConfig",
    "SkillsConfig",
    "ToolsConfig",
    "find_config_file",
    "load_config",
    "parse_mcp_servers",
    "write_mock_config",
]
