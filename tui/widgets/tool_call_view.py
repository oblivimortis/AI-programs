"""工具调用卡片：折叠展示工具名、参数与执行结果。"""

from __future__ import annotations

from textual.widgets import Collapsible, Static

from core.tools.base import ToolResult
from tui.formatting import format_arguments, truncate


class ToolCallView(Collapsible):
    """一次工具调用的可视化卡片。"""

    DEFAULT_CSS = """
    ToolCallView {
        height: auto;
        margin: 0 0 1 0;
        border-left: thick $accent;
        padding: 0 1;
    }
    ToolCallView > .tool-result {
        height: auto;
        color: $text-muted;
    }
    ToolCallView.failed > .tool-result {
        color: $error;
    }
    """

    def __init__(self, name: str, arguments: str = "", **kwargs) -> None:
        # 注意：``_name`` 会被 Textual 的 DOMNode 用于表示控件名，这里使用 ``_tool_name``。
        self._tool_name = name
        self._arguments = arguments
        self._body = Static("", classes="tool-result")
        super().__init__(self._body, title=self._build_title(), collapsed=True, **kwargs)

    def _build_title(self) -> str:
        summary = format_arguments(self._arguments)
        if summary:
            return f"🔧 {self._tool_name}({summary})"
        return f"🔧 {self._tool_name}()"

    def set_result(self, result: ToolResult) -> None:
        """写入工具执行结果。"""
        body = truncate(result.content or "", max_length=4000)
        if not body:
            body = "(无返回内容)"
        self._body.update(body)
        if not result.ok:
            self.add_class("failed")
            self.collapsed = False


__all__ = ["ToolCallView"]
