"""斜杠命令补全面板。"""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from tui.commands import SlashCommand


class CommandPanel(OptionList):
    """输入 ``/`` 时弹出的命令候选列表。

    焦点仍然留在输入框，由屏幕层转发上下键与回车；
    面板只负责显示与记录当前高亮项。
    """

    DEFAULT_CSS = """
    CommandPanel {
        height: auto;
        max-height: 8;
        border: round $accent;
        background: $panel;
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.display = False

    def show(self, commands: list[SlashCommand]) -> None:
        """显示候选列表。"""
        self.clear_options()
        for command in commands:
            self.add_option(Option(f"{command.name}  {command.description}", id=command.name))
        self.display = bool(commands)
        if commands:
            self.highlighted = 0

    def hide(self) -> None:
        """隐藏面板。"""
        self.display = False

    @property
    def is_open(self) -> bool:
        return bool(self.display) and self.option_count > 0

    def move(self, delta: int) -> None:
        """在候选之间移动高亮项。"""
        if self.option_count == 0:
            return
        current = self.highlighted or 0
        self.highlighted = (current + delta) % self.option_count

    def selected_command(self) -> str:
        """返回当前高亮的命令名。"""
        index = self.highlighted
        if index is None or self.option_count == 0:
            return ""
        option = self.get_option_at_index(index)
        return str(option.id or "")


__all__ = ["CommandPanel"]
