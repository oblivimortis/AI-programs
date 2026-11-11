"""上下文状态栏：显示当前会话的上下文占用。"""

from __future__ import annotations

from textual.widgets import Static


class ContextBar(Static):
    """一行上下文占用提示，例如 ``上下文 12.3k / 128k（10%）默认``。"""

    DEFAULT_CSS = """
    ContextBar {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    ContextBar.warning {
        color: $warning;
    }
    ContextBar.critical {
        color: $error;
        text-style: bold;
    }
    """

    def show_status(
        self,
        used_tokens: int,
        window_tokens: int,
        ratio: float,
        window_default: bool = True,
        *,
        warning: bool = False,
        blocked: bool = False,
    ) -> None:
        """更新显示内容与醒目程度。"""
        used = f"{used_tokens / 1000:.1f}k"
        window = f"{window_tokens / 1000:.0f}k"
        text = f"上下文 {used} / {window}（{ratio * 100:.0f}%）"
        if window_default:
            text += " 默认"
        if blocked:
            text += " ⚠ 请执行 /compact"
        elif warning:
            text += " ⚠ 建议执行 /compact"
        self.update(text)
        self.set_class(blocked, "critical")
        self.set_class(warning and not blocked, "warning")


__all__ = ["ContextBar"]
