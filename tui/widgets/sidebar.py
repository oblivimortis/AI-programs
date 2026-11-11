"""侧边栏：展示会话上下文信息（模型、工作区、可用工具）。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown


class Sidebar(VerticalScroll):
    """只读信息面板。"""

    DEFAULT_CSS = """
    Sidebar {
        width: 32;
        height: 1fr;
        border-left: solid $panel;
        padding: 1 1;
        /* 始终预留滚动条位置，避免技能列表变长时内容左右跳动 */
        scrollbar-size-vertical: 3;
        scrollbar-gutter: stable;
        scrollbar-visibility: visible;
        scrollbar-background: $panel;
        scrollbar-background-hover: $panel;
        scrollbar-color: $accent;
        scrollbar-color-hover: $text;
    }
    Sidebar Markdown {
        height: auto;
    }
    """

    def __init__(
        self,
        model: str = "",
        workspace: str = "",
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        mcp: list | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._workspace = workspace
        self._tools = list(tools or ())
        self._skills = list(skills or ())
        self._mcp = list(mcp or ())

    def compose(self) -> ComposeResult:
        yield Markdown(self._markdown_source())

    def _mcp_lines(self) -> str:
        if not self._mcp:
            return "- （未配置）"
        lines: list[str] = []
        for server in self._mcp:
            status = {
                "ready": "已连接",
                "connecting": "连接中…",
                "error": "连接失败",
            }.get(server.status, server.status)
            lines.append(f"- {server.name}：{status}")
            if server.status == "error" and server.error:
                lines.append(f"  - 错误：{server.error[:60]}")
            for tool in server.tools:
                lines.append(f"  - `{tool}`")
        return "\n".join(lines)

    def _markdown_source(self) -> str:
        tool_lines = "\n".join(f"- `{name}`" for name in self._tools) or "- （无）"
        skill_lines = "\n".join(f"- {name}" for name in self._skills) or "- （无）"
        return (
            "**运行信息**\n\n"
            f"- 模型：`{self._model or '未知'}`\n"
            f"- 工作区：`{self._workspace or '未知'}`\n"
            f"- 工具数：{len(self._tools)}\n"
            f"- 技能数：{len(self._skills)}\n"
            f"- MCP 服务器：{len(self._mcp)}\n\n"
            "**可用工具**\n\n"
            f"{tool_lines}\n\n"
            "**可用技能**\n\n"
            f"{skill_lines}\n\n"
            "**MCP 服务器**\n\n"
            f"{self._mcp_lines()}\n\n"
            "**快捷键**\n\n"
            "- `Enter` 发送\n"
            "- `Ctrl+L` 清空对话\n"
            "- `Ctrl+N` 新建会话\n"
            "- `Ctrl+B` 折叠侧栏\n"
            "- `Ctrl+C` 退出"
        )

    def refresh_info(
        self,
        model: str,
        workspace: str,
        tools: list[str],
        skills: list[str] | None = None,
        mcp: list | None = None,
    ) -> None:
        """刷新面板内容（``skills`` / ``mcp`` 为 ``None`` 时保留原值）。"""
        self._model = model
        self._workspace = workspace
        self._tools = list(tools)
        if skills is not None:
            self._skills = list(skills)
        if mcp is not None:
            self._mcp = list(mcp)
        self.query_one(Markdown).update(self._markdown_source())
        # 内容高度变化后重新布局，滚动区域才会跟着更新。
        self.refresh(layout=True)

    def refresh_mcp(self, mcp: list) -> None:
        """只刷新 MCP 区域（异步连接完成后调用）。"""
        self._mcp = list(mcp)
        self.query_one(Markdown).update(self._markdown_source())
        self.refresh(layout=True)


__all__ = ["Sidebar"]
