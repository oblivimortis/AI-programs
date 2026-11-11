"""recallContext：在上下文归档中做本地全文检索。"""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from core.context import MAX_SEARCH_HITS, ContextManager
from core.tools.base import Tool, ToolResult


class RecallContextTool(Tool):
    """检索压缩前保存的原始上下文。"""

    name: ClassVar[str] = "recallContext"
    description: ClassVar[str] = (
        "检索上下文压缩前的原始对话归档，用于找回摘要里缺失的细节。"
        "提供 text（关键词）即可在全部归档中搜索；同时提供 id 时只搜索指定归档编号。"
        "归档保存在项目本地的 .agents/context/ 目录下。"
    )

    def __init__(self, context_manager: ContextManager) -> None:
        self._context = context_manager

    @property
    def context(self) -> ContextManager:
        return self._context

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "可选。上下文归档编号（形如 20261022-153000），"
                        "不提供时检索全部归档。"
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "必需。要查找的关键词或文本片段。",
                },
            },
            "required": ["text"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        text = str(arguments.get("text", "")).strip()
        archive_id = arguments.get("id")
        archive_id = str(archive_id).strip() if archive_id is not None else ""

        if not text:
            return ToolResult.failure("参数 text 不能为空：请提供要检索的关键词。")

        hits = self._context.search(text, archive_id or None, limit=MAX_SEARCH_HITS)
        if not hits:
            scope = f"归档 {archive_id}" if archive_id else "全部归档"
            available = "、".join(self._context.list_archives()) or "（无归档）"
            return ToolResult.failure(
                f"在{scope}中没有找到与「{text}」匹配的内容。已有归档：{available}"
            )

        lines = [f"在归档中检索「{text}」，共找到 {len(hits)} 条相关片段：", ""]
        for index, hit in enumerate(hits, start=1):
            lines.append(
                f"{index}. [归档 {hit['archive']} · 第 {hit['line']} 条 · {hit['role']}]"
            )
            lines.append(hit["snippet"])
            lines.append("")

        return ToolResult.success(
            "\n".join(lines),
            hits=len(hits),
            archives=sorted({hit["archive"] for hit in hits}),
        )


__all__ = ["RecallContextTool"]
