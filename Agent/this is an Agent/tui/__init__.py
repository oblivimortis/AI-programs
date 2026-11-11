"""TUI 表现层：基于 Textual 的终端界面。

分层约定：
    * ``tui.app``     —— 应用外壳
    * ``tui.screens``  —— 功能屏幕（聊天）
    * ``tui.widgets``  —— 可复用组件
    * ``tui.formatting`` —— 纯展示逻辑

tui 层只依赖 ``core`` 的公开 API，不得直接访问网络或文件系统。
"""

from __future__ import annotations

from tui.app import AgentApp

__all__ = ["AgentApp"]
