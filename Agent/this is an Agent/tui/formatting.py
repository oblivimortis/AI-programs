"""TUI 表现层的纯格式化函数。

把 "如何展示" 的逻辑从 Widget 中抽离，便于单元测试，也便于其它前端
（例如 Web）复用同样的展示规则。
"""

from __future__ import annotations

import json
from typing import Any

ELLIPSIS = "…"


def format_arguments(arguments: str | dict[str, Any] | None, max_length: int = 72) -> str:
    """把工具参数格式化为单行摘要。"""
    if arguments is None:
        return ""
    if isinstance(arguments, dict):
        payload: Any = arguments
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    else:
        raw = (arguments or "").strip()
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _truncate(raw, max_length)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    if payload in ({}, [], None):
        return ""
    return _truncate(raw, max_length)


def _truncate(text: str, max_length: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + ELLIPSIS


def truncate(text: str, max_length: int = 2000) -> str:
    """截断过长文本，避免撑爆界面。"""
    if len(text) <= max_length:
        return text
    return text[: max_length] + f"\n...（已截断，共 {len(text)} 字符）"


__all__ = ["format_arguments", "truncate"]
