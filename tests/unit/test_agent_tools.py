"""Tool-wrapper and MCP-bounding checks for the SDK-based agent."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from agents.tool_context import ToolContext
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool

from app.agent import (
    BoundedMCPServer,
    RunArtifacts,
    _apply_repeat_guard,
    filesystem_tools,
)
from app.agent import test_tool as run_tests_tool
from app.contracts import FilesystemOperation, ToolCall
from app.tools.filesystem import FilesystemTool


def _make_repo(tmp_path: Path, file_name: str = "notes.txt", content: str = "hello world") -> Path:
    file_path = tmp_path / file_name
    file_path.write_text(content, encoding="utf-8")
    return tmp_path


def _tools(tmp_path: Path, apply_changes: bool = False) -> dict[str, object]:
    artifacts = RunArtifacts(target_root=tmp_path, apply_changes=apply_changes)
    tools = filesystem_tools(tmp_path, apply_changes)
    if apply_changes:
        tools.append(run_tests_tool(tmp_path))
    for tool in tools:
        _apply_repeat_guard(tool, artifacts)
    return {tool.name: tool for tool in tools}


def _invoke(
    tool: object, artifacts: RunArtifacts, arguments: dict[str, object]
) -> dict[str, object]:
    ctx = ToolContext(
        context=artifacts,
        tool_name=tool.name,
        tool_call_id="c1",
        tool_arguments="{}",  # type: ignore[attr-defined]
    )
    raw = asyncio.run(tool.on_invoke_tool(ctx, json.dumps(arguments)))  # type: ignore[attr-defined]
    return json.loads(raw)


def test_read_only_requests_never_see_mutation_tools(tmp_path: Path) -> None:
    """The structural guardrail: mutating tools are absent, not merely forbidden."""

    tools = _tools(_make_repo(tmp_path), apply_changes=False)

    assert sorted(tools) == ["fs_list", "fs_read"]


def test_authorized_requests_see_mutation_tools(tmp_path: Path) -> None:
    tools = _tools(_make_repo(tmp_path), apply_changes=True)

    assert sorted(tools) == ["fs_create", "fs_edit", "fs_list", "fs_read", "fs_write", "run_tests"]


def test_wrapped_read_stays_within_target_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    artifacts = RunArtifacts(target_root=repo, apply_changes=False)
    payload = _invoke(_tools(repo)["fs_read"], artifacts, {"path": "../secret.txt"})

    assert payload["kind"] == "filesystem"
    assert not payload["result"]["succeeded"]
    assert payload["result"]["error"] == "requested path escapes the target repository"


def test_wrapped_edit_requires_exactly_one_occurrence(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "repeated.txt", "same same")
    artifacts = RunArtifacts(target_root=repo, apply_changes=True)
    payload = _invoke(
        _tools(repo, apply_changes=True)["fs_edit"],
        artifacts,
        {"path": "repeated.txt", "old_text": "same", "new_text": "changed"},
    )

    assert not payload["result"]["succeeded"]
    assert payload["result"]["error"] == "edit operation requires old_text to occur exactly once"
    assert (repo / "repeated.txt").read_text(encoding="utf-8") == "same same"


def test_wrapped_edit_replaces_the_single_occurrence(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    artifacts = RunArtifacts(target_root=repo, apply_changes=True)
    payload = _invoke(
        _tools(repo, apply_changes=True)["fs_edit"],
        artifacts,
        {"path": "notes.txt", "old_text": "world", "new_text": "agent"},
    )

    assert payload["result"]["succeeded"]
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "hello agent"


def test_repeat_guard_blocks_identical_consecutive_calls(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    tools = _tools(repo)
    fs_read = tools["fs_read"]
    first = _invoke(
        fs_read, RunArtifacts(target_root=repo, apply_changes=False), {"path": "notes.txt"}
    )
    second = _invoke(
        fs_read, RunArtifacts(target_root=repo, apply_changes=False), {"path": "notes.txt"}
    )

    assert first["result"]["succeeded"]
    assert "blocked: identical to the previous action" in second["result"]["error"]


class _StubMCPServer:
    """Minimal delegate standing in for a live SDK MCP server."""

    name = "stub"

    def __init__(self, tools: list[McpTool] | Exception) -> None:
        self._tools = tools

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def list_tools(self, run_context: object = None, agent: object = None) -> list[object]:
        if isinstance(self._tools, Exception):
            raise self._tools
        return self._tools

    async def call_tool(
        self, tool_name: str, arguments: dict | None, meta: dict | None = None
    ) -> object:
        return CallToolResult(content=[TextContent(type="text", text="stub")])

    async def list_prompts(self) -> object:
        raise NotImplementedError

    async def get_prompt(self, name: str, arguments: dict | None = None) -> object:
        raise NotImplementedError


def _mcp_tool(name: str, description: str, schema: dict[str, object]) -> object:
    return McpTool(name=name, description=description, input_schema=schema)


def test_bounded_mcp_server_caps_advertisements() -> None:
    from app.agent import _MAX_EXTERNAL_TOOLS

    big_schema = {"type": "object", "properties": {"x": {"description": "y" * 5000}}}
    tools = [
        _mcp_tool(f"tool_{index}", "d" * 1000, big_schema)
        for index in range(_MAX_EXTERNAL_TOOLS + 4)
    ]
    bounded = BoundedMCPServer(_StubMCPServer(tools))

    advertised = asyncio.run(bounded.list_tools())

    assert len(advertised) == _MAX_EXTERNAL_TOOLS
    for tool in advertised:
        assert len(tool.description) <= 300
        assert len(json.dumps(tool.input_schema, sort_keys=True)) <= 2000


def test_bounded_mcp_server_survives_a_failing_list() -> None:

    bounded = BoundedMCPServer(_StubMCPServer(RuntimeError("boom")))

    assert asyncio.run(bounded.list_tools()) == []


def test_bounded_mcp_server_wraps_server_flagged_errors() -> None:

    class ErroringServer(_StubMCPServer):
        async def call_tool(
            self, tool_name: str, arguments: dict | None, meta: dict | None = None
        ) -> object:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="nope")])

    bounded = BoundedMCPServer(ErroringServer(None))
    result = asyncio.run(bounded.call_tool("demo.echo", {}))

    payload = json.loads(result.content[0].text)
    assert payload == {"mcp_error": "nope"}


def test_run_tests_tool_reports_missing_configuration(tmp_path: Path) -> None:
    artifacts = RunArtifacts(target_root=tmp_path, apply_changes=True)
    payload = _invoke(run_tests_tool(tmp_path), artifacts, {})

    assert payload["kind"] == "test"
    assert not payload["result"]["passed"]
    assert "no test command is configured" in payload["result"]["error"]


def test_run_tests_tool_executes_configured_command(tmp_path: Path) -> None:
    (tmp_path / ".coding-agent.toml").write_text(
        f"[test]\ncommand = {json.dumps([sys.executable, '-c', 'print(7)'])}\n",
        encoding="utf-8",
    )
    artifacts = RunArtifacts(target_root=tmp_path, apply_changes=True)
    payload = _invoke(run_tests_tool(tmp_path), artifacts, {})

    assert payload["kind"] == "test"
    assert payload["result"]["passed"] is True
    assert "7" in payload["result"]["output"]


def test_filesystem_tool_still_guards_changes(tmp_path: Path) -> None:
    """Defense in depth: the FilesystemTool itself keeps its authorization check."""

    repo = _make_repo(tmp_path)

    with pytest.raises(ValueError):
        FilesystemTool(tmp_path / "missing-directory")
    result = FilesystemTool(repo, allow_changes=False).execute(
        ToolCall(
            tool_name="filesystem",
            operation=FilesystemOperation.CREATE,
            path=repo / "new.txt",
            arguments={"content": "x"},
        )
    )
    assert not result.succeeded
    assert "permission denied" in result.error
