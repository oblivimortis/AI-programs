"""统一的异常体系。

分层原则：
    * ``core`` 内部只抛出本模块定义的异常；
    * 异常携带足够的信息，便于各前端（TUI/Web/Desktop）以一致的方式呈现。
"""

from __future__ import annotations


class AgentError(Exception):
    """所有 Agent 异常的基类。"""


class ConfigurationError(AgentError):
    """配置缺失或非法。"""


class LLMError(AgentError):
    """大模型调用失败（网络、鉴权、协议错误等）。"""


class ToolError(AgentError):
    """工具执行失败。"""


class ToolNotFoundError(ToolError):
    """请求的工具不存在。"""


class PathPolicyError(ToolError):
    """路径不符合安全策略（例如越过工作区根目录）。"""


__all__ = [
    "AgentError",
    "ConfigurationError",
    "LLMError",
    "ToolError",
    "ToolNotFoundError",
    "PathPolicyError",
]
