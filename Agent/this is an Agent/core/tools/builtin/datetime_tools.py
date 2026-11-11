"""日期 / 时间 / 计算类工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Mapping

from core.tools.base import Tool, ToolResult


class GetDateTool(Tool):
    """获取当前电脑的日期和时间。"""

    name: ClassVar[str] = "getDate"
    description: ClassVar[str] = "获取当前电脑的日期和时间"

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        now = datetime.now()
        text = now.strftime("%Y-%m-%d %H:%M:%S")
        return ToolResult.success(text, iso=now.isoformat(timespec="seconds"))


class IsLeapYearTool(Tool):
    """判断给定年份是否为闰年。"""

    name: ClassVar[str] = "isLeapYear"
    description: ClassVar[str] = "判断输入的年份是否是闰年"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "要判断的年份，例如 2024",
                }
            },
            "required": ["year"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        raw_year = arguments.get("year")
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            return ToolResult.failure(f"年份参数无效: {raw_year!r}")

        # 闰年：能被 4 整除但不能被 100 整除，或者能被 400 整除
        is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
        text = f"{year} {'是' if is_leap else '不是'}闰年"
        return ToolResult.success(text, year=year, is_leap=is_leap)


__all__ = ["GetDateTool", "IsLeapYearTool"]
