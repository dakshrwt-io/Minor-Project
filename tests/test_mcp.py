"""Tests for the MCP client, using the real demo server from examples/."""

import sys

from app.agent import run_agent
from app.mcp_client import McpConnections
from tests.test_agent import FakeClient, collect, text_reply, tool_call

DEMO_SERVER = {"name": "demo", "command": sys.executable, "args": ["examples/demo_mcp_server.py"]}


async def test_demo_server_tools_are_listed_and_callable():
    mcp = McpConnections()
    await mcp.start([DEMO_SERVER])
    try:
        names = [tool["function"]["name"] for tool in mcp.descriptions]
        assert names == ["demo__current_time"]

        result = await mcp.call("demo__current_time", {})
        assert result.startswith("20")  # a date like 2026-09-30 ...
    finally:
        await mcp.stop()


async def test_broken_server_is_reported_not_crashing():
    mcp = McpConnections()
    broken = {"name": "broken", "command": "this-program-does-not-exist"}

    await mcp.start([broken, DEMO_SERVER])
    try:
        assert len(mcp.errors) == 1
        assert "broken" in mcp.errors[0]
        assert "demo__current_time" in mcp.tools  # the good server still works
    finally:
        await mcp.stop()


async def test_agent_can_use_mcp_tool(tmp_path):
    mcp = McpConnections()
    await mcp.start([DEMO_SERVER])
    client = FakeClient([tool_call("demo__current_time", {}), text_reply("It is now ...")])
    try:
        events = await collect(run_agent("what time is it?", str(tmp_path), False, [], client, mcp))
    finally:
        await mcp.stop()

    assert events[0] == {"type": "action", "tool": "demo__current_time", "arguments": {}}
    assert events[1]["result"].startswith("20")
