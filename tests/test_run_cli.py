"""RunCli 工具测试：命令拼装、输出/退出码、超时、截断与错误处理。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from core.config.settings import ToolsConfig
from core.factory import build_tool_registry
from core.tools.builtin.cli_tools import (
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
    RUN_CLI_TIMEOUT,
    RunCliTool,
    _decode_output,
)


def run(coro):
    return asyncio.run(coro)


def _completed(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["echo"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class RunCliToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = RunCliTool()

    def test_success_returns_stdout_stderr_and_exit_code(self) -> None:
        completed = _completed(stdout=b"hello\n", stderr=b"warn\n")

        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            return_value=completed,
        ) as run_mock:
            result = run(
                self.tool.execute({"command": "echo", "args": ["hello"]})
            )

        self.assertTrue(result.ok, result.content)
        self.assertIn("命令: echo", result.content)
        self.assertIn('参数: ["hello"]', result.content)
        self.assertIn("执行状态: 成功", result.content)
        self.assertIn("退出码: 0", result.content)
        self.assertIn("stdout:\nhello", result.content)
        self.assertIn("stderr:\nwarn", result.content)

        self.assertEqual(result.metadata["command"], "echo")
        self.assertEqual(result.metadata["args"], ["hello"])
        self.assertEqual(result.metadata["exit_code"], 0)
        self.assertFalse(result.metadata["timed_out"])
        self.assertFalse(result.metadata["stdout_truncated"])
        self.assertFalse(result.metadata["stderr_truncated"])

        # 必须以参数列表传给 subprocess，并禁用 shell。
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["echo", "hello"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["shell"], False)
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["timeout"], RUN_CLI_TIMEOUT)

    def test_nonzero_exit_code_is_returned_as_failure(self) -> None:
        completed = _completed(
            stdout=b"partial\n", stderr=b"boom\n", returncode=2
        )

        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            return_value=completed,
        ):
            result = run(
                self.tool.execute({"command": "false", "args": []})
            )

        self.assertFalse(result.ok)
        self.assertIn("执行状态: 失败（退出码非 0）", result.content)
        self.assertIn("退出码: 2", result.content)
        self.assertIn("partial", result.content)
        self.assertIn("boom", result.content)
        self.assertEqual(result.metadata["exit_code"], 2)

    def test_timeout_reports_partial_output_and_no_exit_code(self) -> None:
        timeout_error = subprocess.TimeoutExpired(
            cmd=["slow"],
            timeout=RUN_CLI_TIMEOUT,
            output=b"partial out\n",
            stderr=b"partial err\n",
        )

        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            side_effect=timeout_error,
        ):
            result = run(
                self.tool.execute({"command": "slow", "args": []})
            )

        self.assertFalse(result.ok)
        self.assertIn("超时", result.content)
        self.assertIn(str(RUN_CLI_TIMEOUT), result.content)
        self.assertIn("退出码: 无", result.content)
        self.assertIn("partial out", result.content)
        self.assertIn("partial err", result.content)
        self.assertTrue(result.metadata["timed_out"])
        self.assertIsNone(result.metadata["exit_code"])

    def test_missing_command_is_reported(self) -> None:
        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file", "missing-cli"),
        ) as run_mock:
            result = run(
                self.tool.execute({"command": "missing-cli", "args": []})
            )

        run_mock.assert_called_once()
        self.assertFalse(result.ok)
        self.assertIn("找不到可执行文件", result.content)
        self.assertIn("missing-cli", result.content)
        self.assertIn("退出码: 无", result.content)

    def test_permission_error_is_reported(self) -> None:
        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            side_effect=PermissionError(13, "Permission denied", "nope"),
        ):
            result = run(
                self.tool.execute({"command": "nope", "args": []})
            )

        self.assertFalse(result.ok)
        self.assertIn("权限不足", result.content)
        self.assertIn("Permission denied", result.content)

    def test_invalid_arguments_are_rejected_without_running_command(self) -> None:
        cases = [
            ({}, "command"),
            ({"command": "  ", "args": []}, "command"),
            ({"command": "py"}, "args"),
            ({"command": "py", "args": "-V"}, "字符串数组"),
            ({"command": "py", "args": ["-V", 1]}, "args[1]"),
            ({"command": "py\targ", "args": []}, "控制字符"),
            ({"command": "py\x00", "args": []}, "控制字符"),
            ({"command": "py", "args": ["-V\x00"]}, "NUL"),
        ]

        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                with mock.patch(
                    "core.tools.builtin.cli_tools.subprocess.run"
                ) as run_mock:
                    result = run(self.tool.execute(arguments))

                self.assertFalse(result.ok)
                self.assertIn(expected, result.content)
                run_mock.assert_not_called()

    def test_long_output_is_truncated_with_marker(self) -> None:
        completed = _completed(stdout=b"A" * 100, stderr=b"B" * 100)

        with mock.patch(
            "core.tools.builtin.cli_tools.subprocess.run",
            return_value=completed,
        ), mock.patch(
            "core.tools.builtin.cli_tools.MAX_STDOUT_CHARS", 30
        ), mock.patch(
            "core.tools.builtin.cli_tools.MAX_STDERR_CHARS", 20
        ):
            result = run(
                self.tool.execute({"command": "noisy", "args": []})
            )

        self.assertTrue(result.ok, result.content)
        self.assertTrue(result.metadata["stdout_truncated"])
        self.assertTrue(result.metadata["stderr_truncated"])
        self.assertEqual(result.metadata["stdout_chars"], 100)
        self.assertEqual(result.metadata["stderr_chars"], 100)
        self.assertIn("stdout 输出过长，已截断", result.content)
        self.assertIn("stderr 输出过长，已截断", result.content)
        self.assertIn("原始长度 100 字符", result.content)
        self.assertLess(len(result.content), 500)

    def test_real_python_process_returns_output(self) -> None:
        code = (
            "import sys; "
            "print('hello from cli'); "
            "print('warning', file=sys.stderr)"
        )

        result = run(
            self.tool.execute(
                {"command": sys.executable, "args": ["-c", code]}
            )
        )

        self.assertTrue(result.ok, result.content)
        self.assertIn("hello from cli", result.content)
        self.assertIn("warning", result.content)
        self.assertIn("退出码: 0", result.content)
        self.assertEqual(result.metadata["command"], sys.executable)
        self.assertEqual(result.metadata["args"], ["-c", code])

    def test_real_nonzero_exit_code_is_reported(self) -> None:
        code = "import sys; print('before exit'); sys.exit(7)"

        result = run(
            self.tool.execute(
                {"command": sys.executable, "args": ["-c", code]}
            )
        )

        self.assertFalse(result.ok)
        self.assertIn("before exit", result.content)
        self.assertIn("退出码: 7", result.content)
        self.assertEqual(result.metadata["exit_code"], 7)

    def test_decode_output_handles_utf8_and_crlf(self) -> None:
        self.assertEqual(_decode_output(b"hello\r\n"), "hello")
        self.assertEqual(_decode_output("中文输出".encode("utf-8")), "中文输出")
        self.assertEqual(_decode_output(None), "")

    def test_registry_exposes_run_cli_spec(self) -> None:
        registry = build_tool_registry(ToolsConfig(root=Path.cwd()))
        self.assertIn("RunCli", registry.names())

        spec = next(item for item in registry.specs() if item.name == "RunCli")
        self.assertEqual(spec.parameters["required"], ["command", "args"])
        self.assertEqual(spec.parameters["properties"]["command"]["type"], "string")
        self.assertEqual(spec.parameters["properties"]["args"]["type"], "array")
        self.assertEqual(
            spec.parameters["properties"]["args"]["items"], {"type": "string"}
        )


if __name__ == "__main__":
    unittest.main()
