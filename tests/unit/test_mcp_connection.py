import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.config import Settings
from app.contracts import ExternalToolDefinition, ExternalToolResult
from app.mcp.adapter import McpClientAdapter
from app.mcp.connection import (
    McpConfigError,
    McpDiscovery,
    McpServerConfig,
    McpServerConnection,
    open_mcp_servers,
    parse_mcp_servers,
)


def test_parse_mcp_servers_reads_a_valid_json_list() -> None:
    configs = parse_mcp_servers(
        '[{"name":"docs","command":"python","args":["-m","docs_server"]}]'
    )

    assert configs == [
        McpServerConfig(name="docs", command="python", args=["-m", "docs_server"])
    ]


def test_parse_mcp_servers_defaults_to_no_servers() -> None:
    assert parse_mcp_servers(None) == []
    assert parse_mcp_servers("") == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '"docs"',
        '[{"command":"python"}]',
        '[{"name":"docs"}]',
        '[{"name":"docs","command":"python","args":"-m"}]',
    ],
)
def test_parse_mcp_servers_rejects_malformed_configurations(raw: str) -> None:
    with pytest.raises(McpConfigError):
        parse_mcp_servers(raw)


def test_settings_load_configured_mcp_servers() -> None:
    settings = Settings.from_env(
        {
            "AGENT_MCP_SERVERS": '[{"name":"docs","command":"python","args":["-m","docs"]}]'
        }
    )

    assert settings.mcp_servers == (
        McpServerConfig(name="docs", command="python", args=["-m", "docs"]),
    )


def test_settings_reject_malformed_mcp_servers() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"AGENT_MCP_SERVERS": "not json"})


class FakeAdapter(McpClientAdapter):
    def __init__(self, tool_names: list[str]) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self._tool_names = tool_names
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> list[ExternalToolDefinition]:
        return [
            ExternalToolDefinition(
                name=name,
                description=f"Tool {name}.",
                input_schema={"type": "object"},
            )
            for name in self._tool_names
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> ExternalToolResult:
        self.calls.append((tool_name, arguments))
        return ExternalToolResult(tool_name=tool_name, succeeded=True, content=("ok",))

    async def close(self) -> None:
        self.closed = True


def make_connector(
    tools_by_server: dict[str, list[str]],
    failing_servers: set[str] | None = None,
):
    @asynccontextmanager
    async def connector(config: McpServerConfig) -> AsyncIterator[FakeAdapter]:
        if config.name in (failing_servers or set()):
            raise RuntimeError("server did not start")
        adapter = FakeAdapter(tools_by_server.get(config.name, []))
        try:
            yield adapter
        finally:
            adapter.closed = True

    return connector


def test_open_mcp_servers_yields_live_connections_and_reports_failures() -> None:
    configs = [
        McpServerConfig(name="docs", command="python", args=["-m", "docs"]),
        McpServerConfig(name="broken", command="python", args=["-m", "broken"]),
    ]
    connector = make_connector({"docs": ["search_docs"]}, failing_servers={"broken"})

    async def run() -> tuple[McpDiscovery, list[McpServerConnection]]:
        async with open_mcp_servers(configs, connector) as discovery:
            return discovery, discovery.connections

    discovery, connections = asyncio.run(run())

    assert [connection.server_name for connection in connections] == ["docs"]
    assert discovery.errors == ["server broken: server did not start"]
    assert [tool.name for tool in asyncio.run(connections[0].adapter.list_tools())] == [
        "search_docs"
    ]


def test_open_mcp_servers_closes_every_connection_on_exit() -> None:
    connector = make_connector({"docs": ["search_docs"]})

    async def run() -> FakeAdapter:
        async with open_mcp_servers(
            [McpServerConfig(name="docs", command="python", args=[])], connector
        ) as discovery:
            adapter = discovery.connections[0].adapter
            assert not adapter.closed
            return adapter

    adapter = asyncio.run(run())

    assert adapter.closed
