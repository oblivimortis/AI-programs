"""状态条：展示 Agent 当前状态。"""

from __future__ import annotations

from textual.widgets import Static

#: 状态类型 -> 展示样式。
STATUS_STYLES = {
    "ready": ("●", "就绪"),
    "busy": ("◐", "生成中…"),
    "error": ("✖", "出错"),
    "info": ("ℹ", ""),
}


class StatusBar(Static):
    """一行状态提示。"""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    StatusBar.busy {
        color: $accent;
    }
    StatusBar.error {
        color: $error;
    }
    """

    def __init__(self, text: str = "", kind: str = "ready", **kwargs) -> None:
        super().__init__(**kwargs)
        self._kind = kind
        self._text = text
        self.set_status(text, kind)

    def set_status(self, text: str, kind: str = "ready") -> None:
        """更新状态文本。"""
        self._kind = kind
        self._text = text
        icon, default = STATUS_STYLES.get(kind, ("●", ""))
        if not text:
            text = default
        self.update(f"{icon} {text}".strip())
        self.set_class(kind == "busy", "busy")
        self.set_class(kind == "error", "error")


__all__ = ["StatusBar"]
