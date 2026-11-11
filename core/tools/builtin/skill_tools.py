"""Agent Skills 的动态加载工具（loadSkill）。

技能正文不会在启动时进入上下文。模型先通过系统提示词里的技能清单知道
有哪些技能，再在需要时调用本工具读取 ``SKILL.md`` 或技能目录内的附加文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Mapping

from core.skills import SkillCreateResult, SkillLibrary, SkillLoadResult
from core.tools.base import Tool, ToolResult


def _display_path(path: Path, skill_directory: Path) -> str:
    """优先展示相对于技能目录的路径，便于模型再次精确引用。"""
    try:
        return path.relative_to(skill_directory).as_posix()
    except ValueError:
        return str(path)


class LoadSkillTool(Tool):
    """按需加载 Agent Skill 的完整指令或附加文件。"""

    name: ClassVar[str] = "loadSkill"
    description: ClassVar[str] = (
        "加载 Agent Skill 的完整内容。只提供 name 时读取该技能的 SKILL.md；"
        "同时提供 filename 时读取技能目录内的附加文件。"
    )

    def __init__(self, library: SkillLibrary) -> None:
        self._library = library

    @property
    def library(self) -> SkillLibrary:
        return self._library

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill 的名称（来自系统提示词中的技能清单）",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "可选。要加载的附加文件路径或文件名；支持相对于技能目录的路径、"
                        "项目相对路径和绝对路径。只提供文件名时会递归搜索技能目录。"
                    ),
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        name = str(arguments.get("name", "")).strip()
        filename_raw = arguments.get("filename")
        filename = str(filename_raw).strip() if filename_raw is not None else None

        result = self._library.load(name, filename)
        return self._format_result(result, filename)

    def _format_result(self, result: SkillLoadResult, filename: str | None) -> ToolResult:
        if result.ok:
            metadata: dict[str, Any] = {}
            if result.path is not None:
                metadata["path"] = str(result.path)
            if result.skill is not None:
                metadata["skill"] = result.skill.name
                metadata["filename"] = _display_path(
                    result.path or result.skill.path, result.skill.directory
                )
            return ToolResult.success(result.content, **metadata)

        content = result.error or "加载技能失败"
        metadata: dict[str, Any] = {}
        if result.skill is not None:
            metadata["skill"] = result.skill.name

        if result.candidates:
            lines = [
                f"文件名 '{filename}' 在技能 '{result.skill.name if result.skill else ''}' "
                "中匹配到多个文件，请从以下候选路径中选择一个后重新调用 loadSkill："
            ]
            for candidate in result.candidates:
                if result.skill is not None:
                    lines.append(f"- {_display_path(candidate, result.skill.directory)}")
                else:
                    lines.append(f"- {candidate}")
            content = "\n".join(lines)
            metadata["candidates"] = [str(path) for path in result.candidates]

        return ToolResult.failure(content, **metadata)


class CreateSkillTool(Tool):
    """按 Agent Skills 规范创建新技能。"""

    name: ClassVar[str] = "createSkill"
    description: ClassVar[str] = (
        "创建一个新的 Agent Skill。传入 name（只能用小写字母、数字和连字符）和 "
        "skillContext（要写入 SKILL.md 的完整内容，必须以 YAML Front Matter 开头，"
        "包含 name 和 description 两个字段，后面接正文）。"
        "工具会在当前项目的 .agents/skills/<name>/SKILL.md 创建文件；"
        "目标技能已存在时会拒绝创建，避免覆盖原有内容。"
        "创建成功后技能清单立即刷新，不需要重启 Agent，随后可直接用 loadSkill 读取。"
        "如果还需要 references/、scripts/ 等附加文件，请在创建成功后继续调用"
        "现有的 readFile / createFile / makeDir 等文件工具完成。"
    )

    def __init__(self, library: SkillLibrary) -> None:
        self._library = library

    @property
    def library(self) -> SkillLibrary:
        return self._library

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Skill 名称，同时也是目录名：只能使用小写字母、数字和连字符，"
                        "例如 pdf-tools、weekly-report"
                    ),
                },
                "skillContext": {
                    "type": "string",
                    "description": (
                        "要写入 SKILL.md 的完整内容。必须以 YAML Front Matter 开头："
                        "---、name、description、---，然后空一行写正文。"
                        "缺少 name 时工具会自动用参数 name 补齐；缺少 description 会报错。"
                    ),
                },
            },
            "required": ["name", "skillContext"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        name = str(arguments.get("name", "")).strip()
        context = str(arguments.get("skillContext", ""))
        result = self._library.create_skill(name, context)
        return self._format_result(result)

    def _format_result(self, result: SkillCreateResult) -> ToolResult:
        if not result.ok:
            metadata = {}
            if result.name:
                metadata["skill"] = result.name
            return ToolResult.failure(result.error or "创建技能失败", **metadata)

        lines = [f"技能 '{result.name}' 创建成功：{result.path}"]
        if result.refreshed:
            lines.append("技能清单已刷新，本轮对话就可以直接调用 loadSkill 使用。")
        else:
            lines.append("技能文件已写入，但技能清单没有变化，请检查 Front Matter 是否完整。")
        for note in result.notes:
            lines.append(f"注意：{note}")
        lines.append(
            "如需补充 references/、scripts/ 等附加文件，"
            "请继续用文件工具在技能目录内创建。"
        )
        return ToolResult.success(
            "\n".join(lines),
            skill=result.name,
            path=str(result.path),
            refreshed=result.refreshed,
        )


__all__ = ["CreateSkillTool", "LoadSkillTool"]
