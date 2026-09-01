"""Tiny stdio MCP demo server (official SDK v2)."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo-server")


@server.tool()
def echo(text: str) -> str:
    """Echo the supplied text back, proving external tool execution."""
    return f"echo: {text}"


if __name__ == "__main__":
    server.run(transport="stdio")
