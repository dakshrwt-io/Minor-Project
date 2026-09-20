"""MCP server adapters whose advertisements to the model are bounded."""

from __future__ import annotations

import json
from typing import Any

from agents.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHttp
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool

from app.config import McpServerConfig

# External MCP tool advertisement caps: external schemas come from outside
# this codebase and would otherwise be an unbounded prompt-size and injection
# surface. Values match the previous prompt builder.
_MAX_EXTERNAL_TOOLS = 8
_MAX_TOOL_DESCRIPTION_CHARS = 300
_MAX_TOOL_SCHEMA_CHARS = 2000


class BoundedMCPServer(MCPServer):
    """One MCP server whose advertisement to the model is bounded.

    Delegates to a live `MCPServerStdio` / `MCPServerStreamableHttp` and caps
    everything the model sees: tool count, description length, and total
    schema size (oversized schemas are replaced by a bounded placeholder,
    keeping the tool callable by name). Server-flagged tool errors are
    re-wrapped as a JSON marker so the run's observation decoding can report
    them as auditable failures.
    """

    def __init__(self, delegate: MCPServer) -> None:
        super().__init__()
        self._delegate = delegate

    @property
    def name(self) -> str:
        return self._delegate.name

    async def connect(self) -> None:
        await self._delegate.connect()

    async def cleanup(self) -> None:
        await self._delegate.cleanup()

    async def __aenter__(self) -> BoundedMCPServer:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self.cleanup()

    async def list_tools(self, run_context: Any = None, agent: Any = None) -> list[McpTool]:
        try:
            tools = await self._delegate.list_tools(run_context, agent)
        except Exception:
            # A server that connects but then fails listing must not crash the
            # run; advertise zero tools instead.
            return []
        return [self._bounded(tool) for tool in tools[:_MAX_EXTERNAL_TOOLS]]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> Any:
        result = await self._delegate.call_tool(tool_name, arguments, meta)
        is_error = bool(getattr(result, "is_error", getattr(result, "isError", False)))
        if is_error:
            text = "".join(
                block.text
                for block in getattr(result, "content", ())
                if getattr(block, "type", "") == "text"
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"mcp_error": text or "MCP server reported a tool error"}),
                    )
                ]
            )
        return result

    async def list_prompts(self) -> Any:
        return await self._delegate.list_prompts()

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self._delegate.get_prompt(name, arguments)

    def _bounded(self, tool: McpTool) -> McpTool:
        """Return one advertisement with bounded prompt cost.

        An oversized schema is replaced by an empty placeholder rather than
        truncated JSON (which would be invalid) — the tool remains callable by
        name; only its argument documentation is lost.
        """

        description = (tool.description or "")[:_MAX_TOOL_DESCRIPTION_CHARS]
        schema = dict(tool.input_schema or {})
        if len(json.dumps(schema, sort_keys=True)) > _MAX_TOOL_SCHEMA_CHARS:
            schema = {
                "type": "object",
                "description": "input schema omitted: exceeds the advertisement size limit",
                "properties": {},
            }
        return McpTool(name=tool.name, description=description, input_schema=schema)


def build_mcp_server(config: McpServerConfig) -> BoundedMCPServer:
    """Construct one bounded server: streamable-HTTP when configured with a
    ``url``, stdio otherwise (matching the legacy connection layer)."""

    if config.url:
        delegate: MCPServer = MCPServerStreamableHttp(name=config.name, params={"url": config.url})
    else:
        delegate = MCPServerStdio(
            name=config.name, params={"command": config.command, "args": config.args}
        )
    return BoundedMCPServer(delegate)


def _mcp_failure_message(ctx: Any, error: Exception) -> str:
    """Render one MCP tool failure as an auditable model-visible message."""

    return f"MCP tool call failed: {error}"
