"""MCP client: let the agent use tools from other programs.

MCP (Model Context Protocol) is a standard way for a program to offer tools
to an AI agent. An "MCP server" is a small program we start ourselves; we talk
to it through its stdin/stdout ("stdio"). We ask it which tools it has, show
those tools to the model next to our file tools, and forward the model's
calls to the right server.

Each server is configured in .env (AGENT_MCP_SERVERS), for example:
    [{"name": "demo", "command": "python", "args": ["examples/demo_mcp_server.py"]}]
"""

from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MAX_TOOLS_PER_SERVER = 20  # don't let one server flood the model with tools
MAX_DESCRIPTION_CHARS = 1000


class McpConnections:
    """Starts MCP servers, remembers their tools, and closes them at the end."""

    def __init__(self):
        # AsyncExitStack remembers everything we open, so stop() can close it all.
        self.stack = AsyncExitStack()
        self.tools = {}  # "demo__current_time" -> (session, "current_time")
        self.descriptions = []  # tool descriptions sent to the model
        self.errors = []  # servers that failed to start

    async def start(self, servers: list[dict]) -> None:
        """Start every configured server. One broken server never stops the others."""
        for server in servers:
            try:
                await self.start_one(server)
            except Exception as error:
                self.errors.append(f"MCP server '{server.get('name')}' failed: {error}")

    async def start_one(self, server: dict) -> None:
        # 1. Start the server program and connect to its stdin/stdout.
        params = StdioServerParameters(command=server["command"], args=server.get("args", []))
        read, write = await self.stack.enter_async_context(stdio_client(params))
        session = await self.stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # 2. Ask which tools it has and describe them to the model.
        result = await session.list_tools()
        for tool in result.tools[:MAX_TOOLS_PER_SERVER]:
            # Put the server name in front, so two servers can both have a "search" tool.
            full_name = f"{server['name']}__{tool.name}"
            self.tools[full_name] = (session, tool.name)
            self.descriptions.append(
                {
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": (tool.description or "")[:MAX_DESCRIPTION_CHARS],
                        "parameters": tool.input_schema,
                    },
                }
            )

    async def call(self, full_name: str, arguments: dict) -> str:
        """Forward one tool call to its server and return the answer as text."""
        session, tool_name = self.tools[full_name]
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as error:
            return f"Error: {error}"
        text = "\n".join(block.text for block in result.content if block.type == "text")
        if result.is_error:
            return f"Error: {text}"
        return text or "(no output)"

    async def stop(self) -> None:
        """Close every connection and stop the server programs."""
        await self.stack.aclose()
