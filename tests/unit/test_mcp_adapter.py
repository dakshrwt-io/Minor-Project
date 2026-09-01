import asyncio
from dataclasses import dataclass
from typing import Any

from app.mcp.adapter import McpClientAdapter


@dataclass
class FakeMcpTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class FakeListToolsResult:
    tools: list[FakeMcpTool]


@dataclass
class FakeTextContent:
    text: str


@dataclass
class FakeCallToolResult:
    content: list[FakeTextContent]
    structured_content: dict[str, Any] | None = None
    is_error: bool = False


class FakeMcpSession:
    def __init__(self, response: FakeCallToolResult) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def list_tools(self) -> FakeListToolsResult:
        return FakeListToolsResult(
            tools=[
                FakeMcpTool(
                    name="search_docs",
                    description="Search documentation.",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                )
            ]
        )

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> FakeCallToolResult:
        self.calls.append((name, arguments))
        return self._response


def test_adapter_translates_mcp_tool_schemas() -> None:
    adapter = McpClientAdapter(FakeMcpSession(FakeCallToolResult(content=[])))

    tools = asyncio.run(adapter.list_tools())

    assert tools[0].name == "search_docs"
    assert tools[0].description == "Search documentation."
    assert tools[0].input_schema["properties"]["query"]["type"] == "string"


def test_adapter_preserves_mcp_text_and_structured_results() -> None:
    session = FakeMcpSession(
        FakeCallToolResult(
            content=[FakeTextContent(text="Found one result.")],
            structured_content={"count": 1},
        )
    )
    adapter = McpClientAdapter(session)

    result = asyncio.run(adapter.call_tool("search_docs", {"query": "MCP"}))

    assert result.succeeded
    assert result.content == ("Found one result.",)
    assert result.structured_content == {"count": 1}
    assert session.calls == [("search_docs", {"query": "MCP"})]


def test_adapter_returns_an_auditable_mcp_error() -> None:
    adapter = McpClientAdapter(
        FakeMcpSession(FakeCallToolResult(content=[FakeTextContent(text="denied")], is_error=True))
    )

    result = asyncio.run(adapter.call_tool("search_docs", {"query": "MCP"}))

    assert not result.succeeded
    assert result.content == ("denied",)
    assert result.error == "MCP server reported a tool error"


def test_adapter_qualifies_tool_names_with_the_server_prefix() -> None:
    adapter = McpClientAdapter(
        FakeMcpSession(FakeCallToolResult(content=[])), name_prefix="docs"
    )

    tools = asyncio.run(adapter.list_tools())

    assert tools[0].name == "docs.search_docs"


def test_adapter_strips_the_prefix_when_calling_a_qualified_tool() -> None:
    session = FakeMcpSession(FakeCallToolResult(content=[FakeTextContent(text="found")]))
    adapter = McpClientAdapter(session, name_prefix="docs")

    result = asyncio.run(adapter.call_tool("docs.search_docs", {"query": "MCP"}))

    assert result.succeeded
    assert session.calls == [("search_docs", {"query": "MCP"})]


def test_adapter_rejects_a_tool_qualified_for_another_server() -> None:
    adapter = McpClientAdapter(
        FakeMcpSession(FakeCallToolResult(content=[])), name_prefix="docs"
    )

    result = asyncio.run(adapter.call_tool("other.search_docs", {"query": "MCP"}))

    assert not result.succeeded
    assert result.error == (
        "MCP tool call failed: tool 'other.search_docs' does not belong to server 'docs'"
    )
