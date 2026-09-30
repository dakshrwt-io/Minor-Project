"""A tiny MCP server for the demo.

It offers one tool the model cannot do by itself: telling the current time.
The agent starts this program and talks to it through stdin/stdout.

Enable it in .env (all on one line):
    AGENT_MCP_SERVERS=[{"name": "demo", "command": "python",
                        "args": ["examples/demo_mcp_server.py"]}]
"""

from datetime import datetime

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo")


@server.tool()
def current_time() -> str:
    """Return the current date and time on this computer."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    server.run(transport="stdio")
