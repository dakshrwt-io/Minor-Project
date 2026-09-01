"""Open configured stdio MCP servers and keep live sessions for one agent run.

The official MCP SDK is imported lazily so unit tests and non-MCP runs never
require it. Connections stay open for the duration of a request so model-issued
tool calls can execute; the orchestrator closes them when the run finishes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.mcp.adapter import McpClientAdapter


class McpConfigError(ValueError):
    """Raised when a configured MCP server entry is malformed."""


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One configured stdio MCP server, as supplied through settings."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> McpServerConfig:
        """Build a validated configuration from one JSON object."""

        name = data.get("name")
        command = data.get("command")
        args = data.get("args", [])
        if not isinstance(name, str) or not name:
            raise McpConfigError("MCP server entry requires a non-empty string 'name'")
        if not isinstance(command, str) or not command:
            raise McpConfigError("MCP server entry requires a non-empty string 'command'")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise McpConfigError("MCP server 'args' must be a list of strings")
        return cls(name=name, command=command, args=list(args))


def parse_mcp_servers(raw: str | None) -> list[McpServerConfig]:
    """Parse the AGENT_MCP_SERVERS JSON list into validated server configurations."""

    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpConfigError("AGENT_MCP_SERVERS must be valid JSON") from exc
    if not isinstance(payload, list):
        raise McpConfigError("AGENT_MCP_SERVERS must be a JSON list of server entries")
    return [McpServerConfig.from_mapping(entry) for entry in payload]


@dataclass(frozen=True, slots=True)
class McpServerConnection:
    """One live server session, with tool names qualified by the server name."""

    server_name: str
    adapter: McpClientAdapter


@dataclass(frozen=True, slots=True)
class McpDiscovery:
    """Live server connections and per-server startup problems from one opening pass."""

    connections: list[McpServerConnection]
    errors: list[str]


McpConnector = Callable[[McpServerConfig], AbstractAsyncContextManager[McpClientAdapter]]


@asynccontextmanager
async def _stdio_adapter(config: McpServerConfig) -> AsyncIterator[McpClientAdapter]:
    """Open one stdio MCP server and adapt its session to the tool contract."""

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=config.command, args=config.args)
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield McpClientAdapter(session, name_prefix=config.name)


@asynccontextmanager
async def open_mcp_servers(
    configs: Sequence[McpServerConfig],
    connector: McpConnector = _stdio_adapter,
) -> AsyncIterator[McpDiscovery]:
    """Open every configured server and yield the live connections.

    One failed server never blocks the others; its error is reported alongside
    the successfully opened connections. All sessions are closed when the
    requesting context exits.
    """

    connections: list[McpServerConnection] = []
    errors: list[str] = []
    exit_stack = AsyncExitStack()
    for config in configs:
        try:
            adapter = await exit_stack.enter_async_context(connector(config))
            connections.append(McpServerConnection(server_name=config.name, adapter=adapter))
        except Exception as exc:
            errors.append(f"server {config.name}: {exc}")
    try:
        yield McpDiscovery(connections=connections, errors=errors)
    finally:
        await exit_stack.aclose()
