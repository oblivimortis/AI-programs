"""本地命令行执行工具（RunCli）。

设计约束：
    * ``command`` 与 ``args`` 分开传入，最终拼装成 ``list[str]``；
    * 始终使用 ``shell=False``，不把参数拼接成 shell 字符串；
    * 捕获 stdout / stderr / exit code，并对过长输出做截断；
    * 不设置命令白名单、黑名单或审批机制，按当前用户权限执行。

安全提示：本工具仅适合受控教学实验环境。它可以启动本机任意可执行程序，
包括 shell、脚本解释器和网络工具。
"""

from __future__ import annotations

import asyncio
import json
import locale
import subprocess
import time
from typing import Any, ClassVar, Mapping

from core.tools.base import Tool, ToolResult

#: 单次命令的最长执行时间（秒）。超时后 subprocess 会终止进程。
RUN_CLI_TIMEOUT = 30

#: 返回给模型的 stdout 最大字符数。
MAX_STDOUT_CHARS = 12_000

#: 返回给模型的 stderr 最大字符数。
MAX_STDERR_CHARS = 8_000


class RunCliTool(Tool):
    """执行本地 CLI，并返回 stdout、stderr 与退出码。"""

    name: ClassVar[str] = "RunCli"
    description: ClassVar[str] = (
        "执行本地命令行程序（CLI）。command 是程序名或可执行文件路径，"
        "args 是参数数组；使用 subprocess 以 shell=False 方式执行，"
        "返回 stdout、stderr 和退出码。适合运行 py、git、curl、pip 等"
        "已安装工具；不支持需要交互式输入的程序。"
    )

    @classmethod
    def parameters(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "要执行的程序名或可执行文件路径，例如 py、git、curl；"
                        "不要在这里包含参数"
                    ),
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "传给程序的参数数组，每一项都必须是字符串；"
                        "没有参数时传空数组 []"
                    ),
                },
            },
            "required": ["command", "args"],
        }

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            command, args = self._validate_arguments(arguments)
        except ValueError as exc:
            return ToolResult.failure(str(exc))

        argv = [command, *args]
        started = time.monotonic()

        try:
            completed = await asyncio.to_thread(self._run, argv)
        except subprocess.TimeoutExpired as exc:
            return self._timeout_result(
                command, args, exc, time.monotonic() - started
            )
        except FileNotFoundError:
            return self._launch_failure(
                command,
                args,
                "无法启动（找不到可执行文件）",
                (
                    f"找不到可执行文件 {command!r}。请确认程序已安装并加入 PATH；"
                    "command 只能填程序名或路径，参数请放入 args。"
                ),
                time.monotonic() - started,
            )
        except PermissionError as exc:
            return self._launch_failure(
                command,
                args,
                "无法启动（权限不足）",
                f"没有权限执行命令 {command!r}: {exc}。请检查当前用户权限和文件可执行权限。",
                time.monotonic() - started,
            )
        except OSError as exc:
            return self._launch_failure(
                command,
                args,
                "无法启动",
                f"启动命令失败: {type(exc).__name__}: {exc}",
                time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001 - 工具异常必须回传给模型
            return self._launch_failure(
                command,
                args,
                "执行失败",
                f"RunCli 执行失败: {type(exc).__name__}: {exc}",
                time.monotonic() - started,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout_raw = _decode_output(completed.stdout)
        stderr_raw = _decode_output(completed.stderr)
        stdout, stdout_truncated = _truncate_output(
            stdout_raw, MAX_STDOUT_CHARS, "stdout"
        )
        stderr, stderr_truncated = _truncate_output(
            stderr_raw, MAX_STDERR_CHARS, "stderr"
        )

        returncode = completed.returncode
        ok = returncode == 0
        status = "成功" if ok else "失败（退出码非 0）"
        content = _format_result(
            command=command,
            args=args,
            returncode=returncode,
            status=status,
            stdout=stdout,
            stderr=stderr,
        )

        metadata = self._metadata(
            command=command,
            args=args,
            returncode=returncode,
            timed_out=False,
            duration_ms=duration_ms,
            stdout_raw=stdout_raw,
            stderr_raw=stderr_raw,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        if ok:
            return ToolResult.success(content, **metadata)
        return ToolResult.failure(content, **metadata)

    # ------------------------------------------------------------------
    # 参数校验与执行
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_arguments(arguments: Mapping[str, Any]) -> tuple[str, list[str]]:
        raw_command = arguments.get("command")
        if not isinstance(raw_command, str) or not raw_command.strip():
            raise ValueError(
                "RunCli 需要非空的 command 参数（程序名或可执行文件路径）"
            )

        command = raw_command.strip()
        if any(ord(char) < 32 for char in command):
            raise ValueError("command 不能包含换行、制表符或 NUL 等控制字符")

        if "args" not in arguments:
            raise ValueError(
                "RunCli 需要 args 参数；没有参数时请传空数组 []"
            )

        raw_args = arguments.get("args")
        if not isinstance(raw_args, (list, tuple)):
            raise ValueError(
                'args 必须是字符串数组，例如 ["--version"]；没有参数时传 []'
            )

        args: list[str] = []
        for index, value in enumerate(raw_args):
            if not isinstance(value, str):
                raise ValueError(
                    f"args[{index}] 必须是字符串，收到 {type(value).__name__}"
                )
            if "\x00" in value:
                raise ValueError(f"args[{index}] 不能包含 NUL 字符")
            args.append(value)

        return command, args

    @staticmethod
    def _run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=RUN_CLI_TIMEOUT,
            check=False,
            shell=False,
        )

    # ------------------------------------------------------------------
    # 结果组装
    # ------------------------------------------------------------------
    def _timeout_result(
        self,
        command: str,
        args: list[str],
        exc: subprocess.TimeoutExpired,
        elapsed: float,
    ) -> ToolResult:
        stdout_raw = _decode_output(exc.stdout)
        stderr_raw = _decode_output(exc.stderr)
        stdout, stdout_truncated = _truncate_output(
            stdout_raw, MAX_STDOUT_CHARS, "stdout"
        )
        stderr, stderr_truncated = _truncate_output(
            stderr_raw, MAX_STDERR_CHARS, "stderr"
        )

        content = _format_result(
            command=command,
            args=args,
            returncode=None,
            status=f"超时（超过 {RUN_CLI_TIMEOUT} 秒，进程已终止）",
            stdout=stdout,
            stderr=stderr,
        )
        metadata = self._metadata(
            command=command,
            args=args,
            returncode=None,
            timed_out=True,
            duration_ms=int(elapsed * 1000),
            stdout_raw=stdout_raw,
            stderr_raw=stderr_raw,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        return ToolResult.failure(content, **metadata)

    def _launch_failure(
        self,
        command: str,
        args: list[str],
        status: str,
        error: str,
        elapsed: float,
    ) -> ToolResult:
        content = _format_result(
            command=command,
            args=args,
            returncode=None,
            status=status,
            stdout="",
            stderr="",
            error=error,
        )
        metadata = self._metadata(
            command=command,
            args=args,
            returncode=None,
            timed_out=False,
            duration_ms=int(elapsed * 1000),
            stdout_raw="",
            stderr_raw="",
            stdout_truncated=False,
            stderr_truncated=False,
            error=error,
        )
        return ToolResult.failure(content, **metadata)

    @staticmethod
    def _metadata(
        *,
        command: str,
        args: list[str],
        returncode: int | None,
        timed_out: bool,
        duration_ms: int,
        stdout_raw: str,
        stderr_raw: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "command": command,
            "args": args,
            "exit_code": returncode,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "stdout_chars": len(stdout_raw),
            "stderr_chars": len(stderr_raw),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        if error is not None:
            metadata["error"] = error
        return metadata


# ----------------------------------------------------------------------
# 文本辅助函数
# ----------------------------------------------------------------------
def _decode_output(data: bytes | str | None) -> str:
    """解码 CLI 输出。

    先尝试 UTF-8，再尝试系统区域编码，最后回退到 GB18030。
    这样既能处理现代工具的 UTF-8 输出，也能兼容中文 Windows 控制台程序。
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return _normalize_output(data)
    if not data:
        return ""

    encodings = ["utf-8", locale.getpreferredencoding(False), "gb18030"]
    seen: set[str] = set()
    for encoding in encodings:
        if not encoding:
            continue
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return _normalize_output(data.decode(encoding))
        except (LookupError, UnicodeDecodeError):
            continue

    return _normalize_output(data.decode("utf-8", errors="replace"))


def _normalize_output(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def _truncate_output(text: str, limit: int, label: str) -> tuple[str, bool]:
    """保留输出开头和结尾，中间用截断标记替代。"""
    if len(text) <= limit:
        return text, False

    head_length = int(limit * 0.7)
    tail_length = max(0, limit - head_length)
    marker = (
        f"\n…（{label} 输出过长，已截断；原始长度 {len(text)} 字符，"
        f"保留开头 {head_length} 和结尾 {tail_length} 字符）\n"
    )
    tail = text[-tail_length:] if tail_length else ""
    return text[:head_length] + marker + tail, True


def _format_result(
    *,
    command: str,
    args: list[str],
    returncode: int | None,
    status: str,
    stdout: str,
    stderr: str,
    error: str | None = None,
) -> str:
    lines = [
        f"命令: {command}",
        f"参数: {json.dumps(args, ensure_ascii=False)}",
        f"执行状态: {status}",
        f"退出码: {returncode if returncode is not None else '无'}",
    ]
    if error:
        lines.append(f"错误: {error}")
    lines.extend(
        [
            "",
            "stdout:",
            stdout if stdout.strip() else "(空)",
            "",
            "stderr:",
            stderr if stderr.strip() else "(空)",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "MAX_STDERR_CHARS",
    "MAX_STDOUT_CHARS",
    "RUN_CLI_TIMEOUT",
    "RunCliTool",
]
