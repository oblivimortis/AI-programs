"""路径策略：把模型给出的路径解析为安全的本地路径。

策略由 :class:`core.config.settings.ToolsConfig` 驱动：

* ``enforce_root=False``（默认，兼容原脚本行为）：允许绝对路径；
* ``enforce_root=True``（沙箱模式）：所有路径必须位于工作区根目录内。
"""

from __future__ import annotations

from pathlib import Path

from core.errors import PathPolicyError


class PathResolver:
    """路径解析器。"""

    def __init__(self, root: Path, enforce_root: bool = False) -> None:
        self.root = Path(root).expanduser().resolve()
        self.enforce_root = enforce_root

    def resolve(self, path: str | None) -> Path:
        """把（可能为相对路径的）输入解析为绝对路径。

        Raises:
            PathPolicyError: 沙箱模式下路径越界。
        """
        raw = (path or "").strip()
        candidate = Path(raw).expanduser() if raw else self.root
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()

        if self.enforce_root and not self._is_within(resolved, self.root):
            raise PathPolicyError(
                f"路径超出工作区范围，已被拒绝: {resolved}（工作区: {self.root}）"
            )
        return resolved

    def relative_to_root(self, path: Path) -> str:
        """把绝对路径转换为便于展示的相对路径字符串。"""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True


__all__ = ["PathResolver"]
