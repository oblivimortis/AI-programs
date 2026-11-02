"""TUI 入口：``python -m tui``。

同时提供无界面的 ``--prompt`` 模式，用于脚本化调用，
也直观展示 core 层与界面层是解耦的。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from core.errors import AgentError
from core.factory import build_default_agent
from tui.app import AgentApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tui",
        description="基于 Textual 的 Agent 终端界面",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="配置文件路径（默认自动查找 config.ini）",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        default=None,
        help="无界面模式：直接向 Agent 提问并打印结果",
    )
    return parser


async def run_headless(agent, prompt: str) -> int:
    """无界面模式：消费事件流并把结果打印到标准输出。"""
    from core.agent.events import ErrorEvent, MessageDelta, ToolCallStarted

    async for event in agent.run(prompt):
        if isinstance(event, MessageDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCallStarted):
            print(f"\n[tool] {event.name}({event.arguments})", flush=True)
        elif isinstance(event, ErrorEvent):
            print(f"\n[error] {event.message}", file=sys.stderr, flush=True)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        agent, config = build_default_agent(args.config)
    except AgentError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 2

    if args.prompt:
        return asyncio.run(run_headless(agent, args.prompt))

    AgentApp(agent, config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
