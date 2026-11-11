"""本地文件系统工具。

对应原脚本中的：getCwd / listFiles / getFileType / readFile / createFile /
editFile / mkdir / removeDir / removeFile。

所有工具共享一个 :class:`~core.tools.paths.PathResolver`，保证路径处理
策略一致（工作区根目录、沙箱校验等）。
"""

from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any, ClassVar, Mapping

from core.tools.base import Tool, ToolResult
from core.tools.paths import PathResolver


class _FileSystemTool(Tool):
    """文件类工具的公共基类，持有路径解析器。"""

    def __init__(
        self,
        resolver: PathResolver,
        max_read_chars: int = 50_000,
        allow_delete: bool = True,
    ) -> None:
        self._resolver = resolver
        self._max_read_chars = max_read_chars
        self._allow_delete = allow_delete

    @property
    def resolver(self) -> PathResolver:
        return self._resolver

    def _resolve(self, path: str | None) -> Path:
        return self._resolver.resolve(path)


class GetCwdTool(_FileSystemTool):
    """获取当前工作目录。"""

    name: ClassVar[str] = "getCwd"
    description: ClassVar[str] = "获取当前工作目录"

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        return ToolResult.success(str(self._resolver.root), path=str(self._resolver.root))


class ListFilesTool(_FileSystemTool):
    """列出目录下的文件。"""

    name: ClassVar[str] = "listFiles"
    description: ClassVar[str] = "列出指定目录下的所有文件（不包含子目录）"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要列出文件的目录路径，例如 C:/temp 或 .",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._resolve(str(arguments.get("path", ".")))
        if not target.is_dir():
            return ToolResult.failure(f"目录不存在: {target}")
        entries = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        files = [item.name for item in entries if item.is_file()]
        directories = [item.name for item in entries if item.is_dir()]
        if not files and not directories:
            return ToolResult.success(f"目录为空: {target}", path=str(target))
        lines = [f"目录: {target}"]
        if files:
            lines.append("文件:")
            lines.extend(f"  - {name}" for name in files)
        if directories:
            lines.append("子目录:")
            lines.extend(f"  - {name}" for name in directories)
        return ToolResult.success(
            "\n".join(lines),
            path=str(target),
            files=files,
            directories=directories,
        )


class GetFileTypeTool(_FileSystemTool):
    """获取文件类型（扩展名与 MIME 类型）。"""

    name: ClassVar[str] = "getFileType"
    description: ClassVar[str] = "获取指定文件的类型（扩展名和MIME类型）"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._resolve(str(arguments.get("path", "")))
        if not target.exists():
            return ToolResult.failure(f"文件不存在: {target}")
        if target.is_dir():
            return ToolResult.success(f"{target} 是一个目录", is_dir=True)
        extension = target.suffix or "(无扩展名)"
        mime, _ = mimetypes.guess_type(target.name)
        text = f"扩展名: {extension}, MIME类型: {mime or '未知'}"
        return ToolResult.success(
            text, path=str(target), extension=extension, mime=mime or "未知"
        )


class ReadFileTool(_FileSystemTool):
    """读取纯文本文件内容。"""

    name: ClassVar[str] = "readFile"
    description: ClassVar[str] = "获取指定文件的内容（仅支持纯文本格式文件）"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._resolve(str(arguments.get("path", "")))
        if not target.is_file():
            return ToolResult.failure(f"文件不存在: {target}")
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.failure(f"读取文件失败: {exc}", path=str(target))

        truncated = False
        if len(content) > self._max_read_chars:
            content = content[: self._max_read_chars] + "\n...(内容过长，已截断)"
            truncated = True
        return ToolResult.success(
            content, path=str(target), truncated=truncated, size=target.stat().st_size
        )


class CreateFileTool(_FileSystemTool):
    """创建新文件。"""

    name: ClassVar[str] = "createFile"
    description: ClassVar[str] = "根据指定的目录和文件名创建新文件"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "文件所在目录，例如 C:/temp",
                },
                "filename": {
                    "type": "string",
                    "description": "文件名，例如 test.txt",
                },
            },
            "required": ["directory", "filename"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        directory = self._resolve(str(arguments.get("directory", ".")))
        filename = str(arguments.get("filename", "")).strip()
        if not filename:
            return ToolResult.failure("文件名不能为空")
        target = directory / filename
        if target.exists():
            return ToolResult.failure(f"文件已存在: {target}", path=str(target))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"创建文件失败: {exc}", path=str(target))
        return ToolResult.success(f"已创建文件: {target}", path=str(target))


class EditFileTool(_FileSystemTool):
    """写入（覆盖或追加）文本文件内容。"""

    name: ClassVar[str] = "editFile"
    description: ClassVar[str] = (
        "编辑指定文件的内容（仅支持纯文本格式文件），默认覆盖原内容，可选追加"
    )

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt",
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的内容",
                },
                "append": {
                    "type": "boolean",
                    "description": "是否追加到文件末尾，默认 false 表示覆盖",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._resolve(str(arguments.get("path", "")))
        if not target.is_file():
            return ToolResult.failure(f"文件不存在: {target}", path=str(target))
        content = str(arguments.get("content", ""))
        append = bool(arguments.get("append", False))
        mode = "a" if append else "w"
        try:
            with open(target, mode, encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            return ToolResult.failure(f"写入文件失败: {exc}", path=str(target))
        action = "追加" if append else "覆盖"
        return ToolResult.success(
            f"已{action}写入: {target}", path=str(target), append=append, length=len(content)
        )


class MakeDirectoryTool(_FileSystemTool):
    """创建目录。"""

    name: ClassVar[str] = "mkdir"
    description: ClassVar[str] = "创建新目录（包括多级目录）"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "目录路径，例如 C:/temp/newdir",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        target = self._resolve(str(arguments.get("path", "")))
        if target.is_dir():
            return ToolResult.failure(f"目录已存在: {target}", path=str(target))
        try:
            target.mkdir(parents=True)
        except OSError as exc:
            return ToolResult.failure(f"创建目录失败: {exc}", path=str(target))
        return ToolResult.success(f"已创建目录: {target}", path=str(target))


class RemoveDirectoryTool(_FileSystemTool):
    """删除目录（含其中所有内容）。"""

    name: ClassVar[str] = "removeDir"
    description: ClassVar[str] = "删除目录（非空目录也会连同其中所有内容一起删除）"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要删除的目录路径，例如 C:/temp/newdir",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        if not self._allow_delete:
            return ToolResult.failure("删除操作已被配置禁用（tools.allow_delete = false）")
        target = self._resolve(str(arguments.get("path", "")))
        if not target.is_dir():
            return ToolResult.failure(f"目录不存在: {target}", path=str(target))
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return ToolResult.failure(f"删除目录失败: {exc}", path=str(target))
        return ToolResult.success(f"已删除目录(含其中所有内容): {target}", path=str(target))


class RemoveFileTool(_FileSystemTool):
    """删除文件。"""

    name: ClassVar[str] = "removeFile"
    description: ClassVar[str] = "删除指定的文件"

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要删除的文件路径，例如 C:/temp/test.txt",
                }
            },
            "required": ["path"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        if not self._allow_delete:
            return ToolResult.failure("删除操作已被配置禁用（tools.allow_delete = false）")
        target = self._resolve(str(arguments.get("path", "")))
        if not target.is_file():
            return ToolResult.failure(f"文件不存在: {target}", path=str(target))
        try:
            os.remove(target)
        except OSError as exc:
            return ToolResult.failure(f"删除文件失败: {exc}", path=str(target))
        return ToolResult.success(f"已删除文件: {target}", path=str(target))


# ----------------------------------------------------------------------
# 内置工具集合
# ----------------------------------------------------------------------
def build_file_system_tools(
    resolver: PathResolver,
    max_read_chars: int = 50_000,
    allow_delete: bool = True,
) -> list[_FileSystemTool]:
    """构造全部文件类工具实例。"""
    common = {
        "resolver": resolver,
        "max_read_chars": max_read_chars,
        "allow_delete": allow_delete,
    }
    return [
        GetCwdTool(**common),
        ListFilesTool(**common),
        GetFileTypeTool(**common),
        ReadFileTool(**common),
        CreateFileTool(**common),
        EditFileTool(**common),
        MakeDirectoryTool(**common),
        RemoveDirectoryTool(**common),
        RemoveFileTool(**common),
    ]


__all__ = [
    "CreateFileTool",
    "EditFileTool",
    "GetCwdTool",
    "GetFileTypeTool",
    "ListFilesTool",
    "MakeDirectoryTool",
    "ReadFileTool",
    "RemoveDirectoryTool",
    "RemoveFileTool",
    "build_file_system_tools",
]
