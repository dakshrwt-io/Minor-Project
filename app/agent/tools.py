"""Filesystem and test tools exposed to the model as SDK function tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents import FunctionTool, function_tool

from app.contracts import (
    ExternalToolResult,
    FilesystemOperation,
    TestResult,
    ToolCall,
    ToolResult,
)
from app.testing.runner import TestCommand, TestRunner
from app.tools.filesystem import FilesystemTool

# Error text appended as a failed observation when the model repeats an
# identical action back to back: the run must not burn its action budget
# re-doing the same call, and the model must be told why.
_REPEAT_BLOCKED_ERROR = (
    "blocked: identical to the previous action. Choose a different action that "
    "advances the task, or reply with plain text to finish."
)

# Filesystem tool names the model may call, mapped to the operation each name
# performs. Any other tool name is an MCP tool call routed to its server.
_FILESYSTEM_TOOL_OPERATIONS: dict[str, FilesystemOperation] = {
    "fs_list": FilesystemOperation.LIST,
    "fs_read": FilesystemOperation.READ,
    "fs_create": FilesystemOperation.CREATE,
    "fs_write": FilesystemOperation.WRITE,
    "fs_edit": FilesystemOperation.EDIT,
}

# Operations that change the repository. After any of these succeeds, the run
# executes the repo's configured test suite so the model sees the pass/fail
# outcome on its next turn, not just the file result.
_MUTATING_OPERATIONS = frozenset(
    {FilesystemOperation.CREATE, FilesystemOperation.WRITE, FilesystemOperation.EDIT}
)


def _filesystem_payload(result: ToolResult, tests: TestResult | None = None) -> str:
    payload: dict[str, Any] = {"kind": "filesystem", "result": result.model_dump(mode="json")}
    if tests is not None:
        payload["tests"] = tests.model_dump(mode="json")
    return json.dumps(payload)


def _external_payload(
    tool_name: str, *, error: str | None, succeeded: bool, content: tuple[str, ...] = ()
) -> str:
    result = ExternalToolResult(
        tool_name=tool_name,
        succeeded=succeeded,
        content=content,
        error=error,
    )
    return json.dumps({"kind": "external", "result": result.model_dump(mode="json")})


def filesystem_tools(target_root: Path, apply_changes: bool) -> list[FunctionTool]:
    """Expose the FilesystemTool as function tools, gated by apply_changes.

    The change-authorization guardrail is structural: when `apply_changes` is
    False the mutation tools are never constructed, so the model is never
    advertised a mutating capability. Inspection tools are always present.
    """

    fs_tool = FilesystemTool(target_root, allow_changes=apply_changes)

    def _execute(operation: FilesystemOperation, path: str, **arguments: str) -> ToolResult:
        call = ToolCall(
            tool_name="filesystem",
            operation=operation,
            path=Path(path),
            arguments=dict(arguments),
        )
        try:
            return fs_tool.execute(call)
        except Exception as exc:  # defense in depth: auditable failure, never a crash
            return ToolResult(call=call, succeeded=False, error=str(exc))

    def _auto_tests(result: ToolResult, operation: FilesystemOperation) -> TestResult | None:
        if not (result.succeeded and operation in _MUTATING_OPERATIONS):
            return None
        return _run_configured_tests(target_root)

    @function_tool(
        name_override="fs_list",
        description_override=(
            "List entries of a directory inside the target repository. "
            'Use "." to list the repository root.'
        ),
        failure_error_function=None,
    )
    def fs_list(path: str) -> str:
        """List entries of a directory inside the target repository."""

        return _filesystem_payload(_execute(FilesystemOperation.LIST, path))

    @function_tool(
        name_override="fs_read",
        description_override="Read one text file inside the target repository.",
        failure_error_function=None,
    )
    def fs_read(path: str) -> str:
        """Read one text file inside the target repository."""

        return _filesystem_payload(_execute(FilesystemOperation.READ, path))

    tools: list[FunctionTool] = [fs_list, fs_read]
    if apply_changes:

        @function_tool(
            name_override="fs_create",
            description_override="Create one new text file; the path must not exist.",
            failure_error_function=None,
        )
        def fs_create(path: str, content: str) -> str:
            """Create one new text file; the path must not exist."""

            result = _execute(FilesystemOperation.CREATE, path, content=content)
            return _filesystem_payload(result, _auto_tests(result, FilesystemOperation.CREATE))

        @function_tool(
            name_override="fs_write",
            description_override="Replace the full content of one existing text file.",
            failure_error_function=None,
        )
        def fs_write(path: str, content: str) -> str:
            """Replace the full content of one existing text file."""

            result = _execute(FilesystemOperation.WRITE, path, content=content)
            return _filesystem_payload(result, _auto_tests(result, FilesystemOperation.WRITE))

        @function_tool(
            name_override="fs_edit",
            description_override=(
                "Replace one exact occurrence of old_text with new_text in an "
                "existing text file; old_text must occur exactly once."
            ),
            failure_error_function=None,
        )
        def fs_edit(path: str, old_text: str, new_text: str) -> str:
            """Replace one exact occurrence of old_text with new_text in an existing text file."""

            result = _execute(FilesystemOperation.EDIT, path, old_text=old_text, new_text=new_text)
            return _filesystem_payload(result, _auto_tests(result, FilesystemOperation.EDIT))

        tools.extend([fs_create, fs_write, fs_edit])
    return tools


def _run_configured_tests(target_root: Path) -> TestResult | None:
    """Discover and run the repository's opt-in test command (blocking).

    The command comes only from the repo's own `.coding-agent.toml`, never
    from model text, and runs without a shell (see app/testing/runner.py).
    """

    try:
        command: TestCommand | None = TestRunner.discover(target_root)
    except ValueError as exc:
        return TestResult(
            command=[],
            passed=False,
            return_code=None,
            error=f"test configuration error: {exc}",
        )
    if command is None:
        return None
    outcome = TestRunner(target_root, command).run()
    return TestResult(
        command=list(outcome.command.arguments),
        passed=outcome.passed,
        output=outcome.output,
        return_code=outcome.return_code,
        timed_out=outcome.timed_out,
        error=outcome.error,
    )


def test_tool(target_root: Path) -> FunctionTool:
    """Expose the target repository's configured test command as one tool."""

    @function_tool(
        name_override="run_tests",
        description_override=(
            "Run the target repository's configured test command from "
            ".coding-agent.toml (reports an auditable result when none is "
            "configured)."
        ),
        failure_error_function=None,
    )
    def run_tests() -> str:
        """Run the target repository's configured test command."""

        result = _run_configured_tests(target_root)
        if result is None:
            result = TestResult(
                command=[],
                passed=False,
                return_code=None,
                error="no test command is configured (missing .coding-agent.toml [test] section)",
            )
        return json.dumps({"kind": "test", "result": result.model_dump(mode="json")})

    return run_tests
