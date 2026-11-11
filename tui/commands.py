"""斜杠命令定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """一个可输入的斜杠命令。"""

    name: str
    description: str


#: 全部可用命令（补全面板与 /help 共用这份列表）。
SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/new", "新建会话（自动保存当前会话）"),
    SlashCommand("/resume", "恢复历史会话"),
    SlashCommand("/save", "保存当前会话"),
    SlashCommand("/compact", "压缩上下文并归档原始记录"),
    SlashCommand("/status", "查看会话与上下文状态"),
    SlashCommand("/clear", "清空界面（保留后端历史）"),
    SlashCommand("/help", "显示命令列表"),
    SlashCommand("/quit", "退出程序"),
)


def filter_commands(prefix: str) -> list[SlashCommand]:
    """按输入前缀过滤命令（用于补全面板）。"""
    text = (prefix or "").strip().lower()
    if not text.startswith("/"):
        return []
    if " " in text:
        return []
    return [command for command in SLASH_COMMANDS if command.name.startswith(text)]


def find_command(name: str) -> SlashCommand | None:
    """按名字精确查找命令。"""
    target = (name or "").strip().lower()
    for command in SLASH_COMMANDS:
        if command.name == target:
            return command
    return None


__all__ = ["SLASH_COMMANDS", "SlashCommand", "filter_commands", "find_command"]
