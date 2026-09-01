"""Adapt an initialized official MCP client session to the internal tool contract."""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.contracts import ExternalToolDefinition, ExternalToolResult


class McpSession(Protocol):
    """The subset of the official MCP ``ClientSession`` used by this adapter."""

    async def list_tools(self) -> Any:
        """Return the MCP server's advertised tool list."""

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an advertised MCP tool."""


class McpClientAdapter:
    """Expose one initialized MCP session through the common external-tool contract.

    Tool names are qualified with the configured server prefix (``docs.search``),
    which both removes cross-server collisions and lets the orchestrator route a
    model-issued call back to the owning session.
    """

    def __init__(self, session: McpSession, name_prefix: str = "") -> None:
        self._session = session
        self._name_prefix = name_prefix

    async def list_tools(self) -> list[ExternalToolDefinition]:
        """Translate MCP tool schemas into provider-neutral tool definitions."""

        response = await self._session.list_tools()
        tools = getattr(response, "tools", ())
        return [
            ExternalToolDefinition(
                name=self._qualified(tool.name),
                description=getattr(tool, "description", "") or "",
                input_schema=dict(
                    getattr(tool, "input_schema", getattr(tool, "inputSchema", {})) or {}
                ),
            )
            for tool in tools
        ]

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ExternalToolResult:
        """Call an MCP tool and retain text, structured data, and errors for observation."""

        try:
            bare_name = self._unqualified(tool_name)
            response = await self._session.call_tool(bare_name, arguments)
        except Exception as exc:
            return ExternalToolResult(
                tool_name=tool_name,
                succeeded=False,
                error=f"MCP tool call failed: {exc}",
            )

        content = tuple(self._content_text(block) for block in getattr(response, "content", ()))
        structured_content = getattr(
            response, "structured_content", getattr(response, "structuredContent", None)
        )
        is_error = getattr(response, "is_error", getattr(response, "isError", False))
        return ExternalToolResult(
            tool_name=tool_name,
            succeeded=not is_error,
            content=content,
            structured_content=structured_content if isinstance(structured_content, dict) else None,
            error="MCP server reported a tool error" if is_error else None,
        )

    def _qualified(self, name: str) -> str:
        return f"{self._name_prefix}.{name}" if self._name_prefix else name

    def _unqualified(self, qualified_name: str) -> str:
        if not self._name_prefix:
            return qualified_name
        prefix = f"{self._name_prefix}."
        if not qualified_name.startswith(prefix):
            raise ValueError(
                f"tool '{qualified_name}' does not belong to server '{self._name_prefix}'"
            )
        return qualified_name[len(prefix) :]

    @staticmethod
    def _content_text(block: Any) -> str:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
        return json.dumps(block, default=str, sort_keys=True)
