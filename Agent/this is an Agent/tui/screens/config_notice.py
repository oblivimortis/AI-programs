"""首次启动提示：缺少 config.ini 时创建示例配置并弹窗提醒。"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class ConfigNoticeApp(App[None]):
    """一个只显示提示文字的简单窗口，按 Enter 退出。"""

    TITLE = "xAgent"
    CSS = """
    Screen { align: center middle; }
    #notice {
        width: 70;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }
    """

    def __init__(self, config_path: str | Path) -> None:
        super().__init__()
        self._path = str(config_path)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "首次启动：已创建一个新的配置文件\n\n"
                f"{self._path}\n\n"
                "其中是示例（mock）信息，程序还无法正常工作。\n"
                "请填写真实的模型 base_url / api_key / model，然后重新启动。\n\n"
                "按 Enter 退出。",
                id="notice",
            )

    def on_key(self, event) -> None:  # noqa: ANN001 - Textual 事件
        if event.key in ("enter", "escape", "ctrl+c", "q"):
            self.exit()


__all__ = ["ConfigNoticeApp"]
