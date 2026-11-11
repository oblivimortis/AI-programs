"""会话保存、AGENTS.md 注入、配置生成与 MCP 的离线测试。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from core.agent.runner import AGENTS_MD_PREFIX, Agent
from core.agent.session import ConversationSession
from core.config.settings import (
    MCPConfig,
    MCPStdioServer,
    load_config,
    write_mock_config,
)
from core.llm.types import Message, StreamDelta
from core.mcp import MCPManager
from core.session_store import SessionStore
from core.tools.registry import ToolRegistry


class FakeClient:
    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, messages, tools=()):
        del messages, tools
        yield StreamDelta(content="ok")


def build_session() -> ConversationSession:
    session = ConversationSession(system_prompt="系统")
    session.add_user("第一个问题")
    session.add_assistant("第一个回答")
    return session


class SessionStoreTests(unittest.TestCase):
    def test_save_list_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = store.save(build_session(), "20261105-100000")
            self.assertEqual(meta.id, "20261105-100000")
            self.assertIn("第一个问题", meta.title)

            metas = store.list()
            self.assertEqual(len(metas), 1)

            messages, injected = store.load(meta.id)
            self.assertEqual([m.content for m in messages], ["第一个问题", "第一个回答"])
            self.assertFalse(injected)

    def test_title_comes_from_first_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            meta = store.save(build_session())
            self.assertEqual(meta.title, "第一个问题")


class AgentsMdTests(unittest.TestCase):
    def test_inject_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text("项目规则：测试必须通过。", encoding="utf-8")
            agent = Agent(client=FakeClient(), registry=ToolRegistry())

            self.assertTrue(agent.inject_agents_md(path))
            self.assertFalse(agent.inject_agents_md(path))
            self.assertEqual(len(agent.session.messages), 1)
            self.assertTrue(agent.session.messages[0].content.startswith(AGENTS_MD_PREFIX))
            self.assertIn("测试必须通过", agent.session.messages[0].content)

    def test_load_session_marks_agents_md_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(client=FakeClient(), registry=ToolRegistry())
            messages = [Message(role="user", content=f"{AGENTS_MD_PREFIX}\n规则")]
            agent.load_session(messages)
            self.assertTrue(agent.session.agents_md_injected)


class MockConfigTests(unittest.TestCase):
    def test_write_mock_config_is_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_mock_config(Path(tmp) / "config.ini")
            self.assertTrue(path.is_file())
            config = load_config(path)
            self.assertEqual(config.llm.model, "mock-model")
            self.assertEqual(config.context.window_tokens, 128000)
            self.assertFalse(config.mcp.enabled)


FAKE_SERVER = '''
import json
import sys


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if "id" not in message:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [
            {"name": "echo", "description": "回显",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}})
    elif method == "tools/call":
        text = message["params"]["arguments"].get("text", "")
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "content": [{"type": "text", "text": "echo:" + text}]}})
    else:
        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
'''


class MCPTests(unittest.TestCase):
    def test_connect_list_and_call_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "fake_server.py"
            server.write_text(FAKE_SERVER, encoding="utf-8")
            config = MCPConfig(
                enabled=True,
                servers=(
                    MCPStdioServer(
                        name="fake",
                        command=sys.executable,
                        args=(str(server),),
                    ),
                ),
                connect_timeout=10.0,
            )
            manager = MCPManager(config)

            async def scenario():
                await manager.start_all()
                self.assertEqual(manager.states()[0].status, "ready")
                self.assertIn("echo", manager.states()[0].tools)

                registry = ToolRegistry()
                count = manager.register_tools(registry)
                self.assertEqual(count, 1)

                tool = registry.get("mcp__fake__echo")
                self.assertIsNotNone(tool)
                result = await tool.execute({"text": "hi"})
                self.assertTrue(result.ok)
                self.assertIn("echo:hi", result.content)
                await manager.close_all()

            asyncio.run(scenario())

    def test_disabled_manager_registers_nothing(self) -> None:
        manager = MCPManager(MCPConfig(enabled=False))
        self.assertFalse(manager.enabled)
        registry = ToolRegistry()
        self.assertEqual(manager.register_tools(registry), 0)


if __name__ == "__main__":
    unittest.main()
