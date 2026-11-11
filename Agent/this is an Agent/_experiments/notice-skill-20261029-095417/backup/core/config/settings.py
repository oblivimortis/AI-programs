"""配置层：把 ``config.ini`` / 环境变量转换为强类型的配置对象。

设计要点：
    * 配置对象是 **不可变** 的（frozen dataclass），避免运行期被意外篡改；
    * 优先级：显式参数 > 环境变量 > ``config.ini`` > 内置默认值；
    * 该模块不依赖任何 UI 框架，可被任意前端复用。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.errors import ConfigurationError

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
class SearchConfig:
    """网络搜索（百度千帆智能搜索生成）参数。"""

    #: 百度千帆 API Key，空字符串表示未配置。
    baidu_qianfan_api_key: str = ""
    #: 智能搜索生成使用的模型；不传模型时 API 只会执行普通搜索，不生成摘要。
    model: str = DEFAULT_SEARCH_MODEL
    #: 单次搜索请求超时时间（秒）。智能搜索生成包含模型总结，通常比普通 HTTP 慢。
    request_timeout: float = 60.0


@dataclass(frozen=True)
class AppConfig:
    """应用级配置聚合根。"""

    llm: LLMConfig
    tools: ToolsConfig
    #: 网络搜索配置。
    search: SearchConfig = field(default_factory=SearchConfig)
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

    return AppConfig(
        llm=llm,
        tools=tools,
        search=search,
        system_prompt=system_prompt,
        source_path=config_path,
    )


__all__ = [
    "AppConfig",
    "LLMConfig",
    "SearchConfig",
    "ToolsConfig",
    "find_config_file",
    "load_config",
]
