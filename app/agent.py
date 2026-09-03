"""The coding agent built on the OpenAI Agents SDK.

A single `Agent` driven by `Runner.run_streamed()` replaces the hand-rolled
router, planner, prompt builder, ReAct loop, model router, and MCP client
adapter:

- `LitellmModel` (selected by AGENT_MODEL_PROVIDER) replaces app/models/.
- `MCPServerStdio` / `MCPServerStreamableHttp` behind `BoundedMCPServer`
  replace app/mcp/.
- `@function_tool` wrappers over `FilesystemTool` / `TestRunner` replace
  app/prompts/builder.py and the loop's tool dispatch table.
- `BoundedSession` (an SDK `Session` over SQLiteSession) replaces app/memory/.
- The streamed run's item events replace the notify() callback.

Safety guarantees are structural, not prompt instructions:

- Path confinement lives in FilesystemTool and is re-checked on every call.
- When `apply_changes=False` the mutating tools are never built, so the model
  is never advertised a mutating capability.
- The bounded action budget maps to `max_turns`; `MaxTurnsExceeded` becomes
  the FAILED "hit the limit" response.
- External MCP tool advertisements are bounded in tool count, description
  length, and schema size before they reach the model.
- Repeated identical actions are blocked between turns (the SDK has no
  built-in equivalent), and every tool failure is rendered as an auditable
  model-visible result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents import (
    Agent,
    FunctionTool,
    RunConfig,
    Runner,
    SQLiteSession,
    function_tool,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import ItemHelpers, ToolCallItem, ToolCallOutputItem
from agents.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHttp
from agents.memory.session import SessionABC
from agents.stream_events import RunItemStreamEvent
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool

from app.config import McpServerConfig, ModelProvider, Settings
from app.contracts import (
    AgentRequest,
    AgentResponse,
    ExternalToolResult,
    FilesystemOperation,
    TaskPlan,
    TaskStatus,
    TestResult,
    ToolCall,
    ToolResult,
)
from app.intelligence.python_analyzer import PythonProjectAnalyzer
from app.intelligence.summary import PythonProjectSummarizer
from app.testing.runner import TestCommand, TestRunner
from app.tools.filesystem import FilesystemTool

# Tracing exports would leave the process; this service stays self-contained.
set_tracing_disabled(True)

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

# External MCP tool advertisement caps: external schemas come from outside
# this codebase and would otherwise be an unbounded prompt-size and injection
# surface. Values match the previous prompt builder.
_MAX_EXTERNAL_TOOLS = 8
_MAX_TOOL_DESCRIPTION_CHARS = 300
_MAX_TOOL_SCHEMA_CHARS = 2000

# Cross-request conversation window: older session items are hidden from the
# model so one long REPL session cannot grow the prompt without bound.
_MAX_SESSION_ITEMS = 40

SYSTEM_INSTRUCTIONS_TEMPLATE = """You are an autonomous coding agent working inside \
target_root, a single repository directory. Complete the user's task there, and nothing else.

How to work:
- conversation_history holds earlier turns of this same session; the current \
task may be a follow-up that refers to them. Treat anything the user said \
earlier as part of the task.
- Act when ready. Inspect only the files the task needs, then act. Do not \
survey the whole repository; once you know a file's path, read it instead of \
listing its parent directory again.
- Every tool call costs one action from a hard budget (see action_budget in \
the task context). When the budget runs out the run fails, so make each \
action the single highest-value step.
- Never repeat an identical action. A repeated call is blocked and wasted; \
each action must produce new information or new progress.
- Base every decision on what files actually contain, never on guesses.
- Make minimal changes: edit exactly what the task requires. No refactors, no \
unrelated edits, no comments, no new files unless the task needs one.

Boundaries you cannot cross:
- Operate only inside target_root.
- There are no shell, deletion, or network capabilities. If the task requires \
them, do the part you can, then reply with text explaining what must be done \
manually instead of trying alternative tools.
{change_policy}
{external_note}
Finishing:
- After a successful mutation the repository's tests run automatically and \
arrive as observations; if they fail and the failure is yours, fix it.
- As soon as the task is satisfied, stop calling tools and reply with plain \
text only. Summarize in 1-3 sentences: what was done, the outcome, and what \
you verified.
"""

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


class BoundedSession(SessionABC):
    """Conversation memory over the SDK's SQLiteSession with bounded replay.

    The old SessionStore clipped both sides of each stored turn (800 chars)
    so conversation history stays context, not a transcript replay; this
    Session subclass preserves that clipping, hides items beyond a fixed
    window from the model, and lets SQLiteSession own storage and eviction.
    """

    MAX_MESSAGE_CHARS = 800
    MAX_REPLY_CHARS = 800

    def __init__(self, session_id: str, db_path: str | Path) -> None:
        self.session_id = session_id
        self._delegate = SQLiteSession(session_id, db_path)

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = await self._delegate.get_items(limit)
        if limit is not None or len(items) <= _MAX_SESSION_ITEMS:
            return items
        windowed = items[-_MAX_SESSION_ITEMS:]
        # Never open the window with an orphaned tool output: chat-completions
        # providers reject a tool result whose call is not present.
        while windowed and windowed[0].get("type") == "function_call_output":
            windowed = windowed[1:]
        return windowed

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        clipped: list[dict[str, Any]] = []
        for item in items:
            role = item.get("role") if isinstance(item, dict) else None
            if role == "user" and isinstance(item.get("content"), str):
                item = {**item, "content": _clip(item["content"], self.MAX_MESSAGE_CHARS)}
            elif role == "assistant" and isinstance(item.get("content"), str):
                item = {**item, "content": _clip(item["content"], self.MAX_REPLY_CHARS)}
            clipped.append(item)
        await self._delegate.add_items(clipped)

    async def pop_item(self) -> dict[str, Any] | None:
        return await self._delegate.pop_item()

    async def clear_session(self) -> None:
        await self._delegate.clear_session()


@dataclass
class RunArtifacts:
    """Mutable per-run state shared by the runner and its tools."""

    target_root: Path
    apply_changes: bool
    repository_summary: str = ""
    # Every decoded tool outcome, in call order; serialized into the response.
    observations: list[ToolResult | TestResult | ExternalToolResult] = field(default_factory=list)
    # Canonical signature of the previous model-issued action, used by the
    # repeat guard to block back-to-back duplicate calls.
    last_signature: str | None = None


def build_model(settings: Settings) -> LitellmModel:
    """Select the provider via LiteLLM, replacing the old model router.

    The key check happens before any error handling so configuration problems
    propagate to the caller instead of being masked downstream.
    """

    if settings.model_provider is ModelProvider.ANTHROPIC:
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required for the Anthropic provider; "
                "set it in the gateway terminal, add it to .env in the repository "
                "root, then restart uvicorn"
            )
        return LitellmModel(
            model=f"anthropic/{settings.model_name}",
            api_key=settings.anthropic_api_key,
            base_url=settings.model_base_url,
        )
    if not settings.deepseek_api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY is required for the DeepSeek provider; "
            "set it in the gateway terminal, add it to .env in the repository "
            "root, then restart uvicorn"
        )
    return LitellmModel(
        model=f"deepseek/{settings.model_name}",
        api_key=settings.deepseek_api_key,
        base_url=settings.model_base_url or "https://api.deepseek.com",
    )


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


def _apply_repeat_guard(tool: FunctionTool, artifacts: RunArtifacts) -> None:
    """Wrap one tool's invocation with the repeat-action guard.

    The old ReAct loop detected back-to-back duplicate model-issued calls and
    fed the model a failed observation instead of executing the call. The SDK
    has no built-in equivalent, so the guard is enforced here as a tool
    wrapper around filesystem, test, and MCP tools alike.
    """

    original = tool.on_invoke_tool

    async def guarded(ctx: Any, input_json: str) -> Any:
        signature = f"{tool.name}:{input_json}"
        if signature == artifacts.last_signature:
            if tool.name in _FILESYSTEM_TOOL_OPERATIONS:
                return _filesystem_payload(
                    ToolResult(
                        call=ToolCall(
                            tool_name="filesystem",
                            operation=_FILESYSTEM_TOOL_OPERATIONS[tool.name],
                            path=Path("."),
                            arguments={},
                        ),
                        succeeded=False,
                        error=_REPEAT_BLOCKED_ERROR,
                    )
                )
            return _external_payload(tool.name, error=_REPEAT_BLOCKED_ERROR, succeeded=False)
        artifacts.last_signature = signature
        try:
            return await original(ctx, input_json)
        except Exception as exc:  # auditable failure instead of a crashed run
            return _external_payload(tool.name, error=f"tool call failed: {exc}", succeeded=False)

    tool.on_invoke_tool = guarded
    tool._repeat_guarded = True  # type: ignore[attr-defined]


class GuardedAgent(Agent[RunArtifacts]):
    """Agent whose full tool set — including MCP tools — is repeat-guarded.

    MCP tools are converted from live server advertisements on every turn, so
    the guard is applied inside `get_all_tools` where the converted tools are
    available.
    """

    async def get_all_tools(self, run_context: Any) -> list[Any]:
        tools = await super().get_all_tools(run_context)
        artifacts: RunArtifacts = run_context.context
        for tool in tools:
            if isinstance(tool, FunctionTool) and not getattr(tool, "_repeat_guarded", False):
                _apply_repeat_guard(tool, artifacts)
        return tools


def build_agent(
    settings: Settings,
    artifacts: RunArtifacts,
    servers: list[BoundedMCPServer],
    mcp_errors: list[str],
    model: Any | None = None,
) -> tuple[GuardedAgent, Any]:
    """Build the single Agent with its tools, model, and instructions.

    `model` overrides settings-driven construction; tests supply a scripted
    SDK Model through it.
    """

    tools = filesystem_tools(artifacts.target_root, artifacts.apply_changes)
    if artifacts.apply_changes:
        tools.append(test_tool(artifacts.target_root))
    model = model or build_model(settings)
    change_policy = (
        "Mutation tools are advertised because this request authorized changes."
        if artifacts.apply_changes
        else "This request did not authorize changes: only inspection tools are advertised, "
        "so inspect the repository and report a proposed change instead."
    )
    external_note = ""
    if servers:
        external_note = (
            "External tools are advertised alongside the filesystem tools; call them by name.\n"
        )
    if mcp_errors:
        external_note += (
            "Some configured MCP servers failed to start and their tools are "
            f"unavailable: {'; '.join(mcp_errors)}\n"
        )
    instructions = SYSTEM_INSTRUCTIONS_TEMPLATE.format(
        change_policy=change_policy, external_note=external_note
    )
    agent = GuardedAgent(
        name="CodingAgent",
        instructions=instructions,
        model=model,
        tools=tools,
        mcp_servers=list(servers),
        mcp_config={
            # Server-qualified tool names make cross-server collisions impossible
            # (the legacy adapter prefixed names with the server name).
            "include_server_in_tool_names": True,
            "failure_error_function": _mcp_failure_message,
        },
    )
    return agent, model


def _mcp_failure_message(ctx: Any, error: Exception) -> str:
    """Render one MCP tool failure as an auditable model-visible message."""

    return f"MCP tool call failed: {error}"


def _decode_tool_output(
    tool_name: str, output: Any
) -> list[ToolResult | TestResult | ExternalToolResult]:
    """Decode one tool output into auditable observation records.

    Filesystem and test tools emit a JSON envelope built by this module; MCP
    outputs are free-form and become ExternalToolResults. Decoding failures
    fall back to a plain-content external result, so every tool call produces
    an observable outcome.
    """

    text = output if isinstance(output, str) else json.dumps(output, default=str)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        kind = data.get("kind")
        try:
            if kind == "filesystem":
                records: list[ToolResult | TestResult | ExternalToolResult] = [
                    ToolResult.model_validate(data["result"])
                ]
                tests = data.get("tests")
                if tests is not None:
                    records.append(TestResult.model_validate(tests))
                return records
            if kind == "test":
                return [TestResult.model_validate(data["result"])]
            if kind == "external":
                return [ExternalToolResult.model_validate(data["result"])]
            if isinstance(data.get("mcp_error"), str):
                return [
                    ExternalToolResult(
                        tool_name=tool_name, succeeded=False, error=data["mcp_error"]
                    )
                ]
        except (KeyError, ValueError):
            pass
    succeeded = not text.startswith("MCP tool call failed:")
    return [
        ExternalToolResult(
            tool_name=tool_name,
            succeeded=succeeded,
            content=_external_content(output, text) if succeeded else (),
            error=None if succeeded else text,
        )
    ]


def _external_content(output: Any, text: str) -> tuple[str, ...]:
    """Extract human-readable content blocks from a raw MCP tool output.

    The SDK renders MCP text content as {"type": "text", "text": ...} dicts
    (a single dict, or a list of them); join those texts instead of
    serializing the envelope.
    """

    blocks = output if isinstance(output, list) else [output]
    texts = [
        block.get("text")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    ]
    if texts:
        return tuple(str(text_value) for text_value in texts)
    return (text,)


def _raw_arguments(item: ToolCallItem) -> dict[str, Any]:
    """Best-effort parse of one model-issued call's arguments for the audit trail."""

    raw = item.raw_item
    arguments = getattr(raw, "arguments", None)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {"raw": arguments}
        except json.JSONDecodeError:
            return {"raw": arguments}
    if isinstance(arguments, dict):
        return arguments
    return {}


class AgentRunner:
    """Drive one AgentRequest through `Runner.run_streamed()`.

    Both gateway transports share one event pipeline: the run is always
    streamed, progress events are forwarded to an optional emitter, and the
    finished `AgentResponse` is decoded from the run result.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        model: Any | None = None,
        repository_analyzer: PythonProjectAnalyzer | None = None,
        repository_summarizer: PythonProjectSummarizer | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._repository_analyzer = repository_analyzer or PythonProjectAnalyzer()
        self._repository_summarizer = repository_summarizer or PythonProjectSummarizer()
        if settings.session_db_path != ":memory:":
            Path(settings.session_db_path).parent.mkdir(parents=True, exist_ok=True)

    async def run(
        self, request: AgentRequest, emit: ProgressEmitter | None = None
    ) -> AgentResponse:
        """Execute one agent request against its explicitly supplied target root.

        Raises ValueError for configuration problems (mapped to HTTP 400) and
        RuntimeError for provider failures (mapped to HTTP 502), matching the
        previous orchestrator's error contract.
        """

        return await self._run_core(request, emit)

    async def run_events(self, request: AgentRequest) -> AsyncIterator[dict[str, Any]]:
        """Yield the full SSE event stream for one run, ending in done or error."""

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def drive() -> None:
            try:
                response = await self._run_core(request, emit)
            except ValueError as exc:
                await queue.put({"type": "error", "status_code": 400, "detail": str(exc)})
            except Exception as exc:
                # RuntimeError and anything unexpected: never leave the stream
                # hanging without a terminal event.
                await queue.put({"type": "error", "status_code": 502, "detail": str(exc)})
            else:
                await queue.put(
                    {
                        "type": "done",
                        "status": response.status.value,
                        "summary": response.summary,
                        "response": response.model_dump(mode="json"),
                    }
                )

        task = asyncio.create_task(drive())
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("type") in {"done", "error"}:
                    break
        finally:
            if not task.done():
                task.cancel()

    async def _run_core(self, request: AgentRequest, emit: ProgressEmitter | None) -> AgentResponse:
        session_id = request.session_id or str(uuid4())
        target_root = request.target_repo.resolve()
        repository_summary = await asyncio.to_thread(self._summarize_repository, target_root)
        artifacts = RunArtifacts(
            target_root=target_root,
            apply_changes=request.apply_changes,
            repository_summary=repository_summary,
        )
        session = BoundedSession(session_id, self._settings.session_db_path)

        async def notify(event: dict[str, Any]) -> None:
            if emit is not None:
                await emit(event)

        async with AsyncExitStack() as stack:
            servers, mcp_errors = await self._open_mcp_servers(stack)
            agent, _model = build_agent(
                self._settings, artifacts, servers, mcp_errors, model=self._model
            )
            plan = TaskPlan(goal=request.task)
            await notify(
                {
                    "type": "plan",
                    "session_id": session_id,
                    "plan": plan.model_dump(mode="json"),
                }
            )
            try:
                streamed = Runner.run_streamed(
                    agent,
                    _user_payload(request.task, artifacts, self._settings.max_agent_iterations),
                    context=artifacts,
                    session=session,
                    max_turns=self._settings.max_agent_iterations,
                    run_config=RunConfig(workflow_name="coding-agent"),
                )
                call_names: dict[str, str] = {}
                async for event in streamed.stream_events():
                    if not isinstance(event, RunItemStreamEvent):
                        continue
                    if event.name == "tool_called" and isinstance(event.item, ToolCallItem):
                        call_names[event.item.call_id or ""] = event.item.tool_name or ""
                        await notify(
                            {
                                "type": "action",
                                "name": event.item.tool_name or "",
                                "arguments": _raw_arguments(event.item),
                            }
                        )
                    elif event.name == "tool_output" and isinstance(event.item, ToolCallOutputItem):
                        tool_name = call_names.get(event.item.call_id or "", "")
                        for observation in _decode_tool_output(tool_name, event.item.output):
                            artifacts.observations.append(observation)
                            await notify(
                                {
                                    "type": "observation",
                                    "observation": observation.model_dump(mode="json"),
                                }
                            )
            except MaxTurnsExceeded:
                limit = self._settings.max_agent_iterations
                summary = f"Stopped after reaching the {limit}-action limit."
                return self._finished(session_id, plan, artifacts, TaskStatus.FAILED, summary)
            except ValueError:
                # Configuration problems keep the HTTP-400 contract.
                raise
            except Exception as exc:
                # Provider, session, and SDK failures map to the same
                # HTTP-502 contract the old provider adapters produced.
                raise RuntimeError(f"agent run failed: {exc}") from exc

            summary = self._final_summary(streamed)
            return self._finished(session_id, plan, artifacts, TaskStatus.COMPLETED, summary)

    async def _open_mcp_servers(
        self, stack: AsyncExitStack
    ) -> tuple[list[BoundedMCPServer], list[str]]:
        """Connect every configured server; one failure never blocks the others."""

        servers: list[BoundedMCPServer] = []
        errors: list[str] = []
        for config in self._settings.mcp_servers:
            server = build_mcp_server(config)
            try:
                await server.connect()
            except Exception as exc:
                errors.append(f"server {config.name}: {exc}")
            else:
                stack.push_async_callback(server.cleanup)
                servers.append(server)
        return servers, errors

    def _final_summary(self, streamed: Any) -> str:
        """Extract the model's final text, mirroring the old loop's contract."""

        final_output = streamed.final_output
        if isinstance(final_output, str) and final_output.strip():
            return final_output.strip()
        text = ItemHelpers.text_message_outputs(streamed.new_items).strip()
        return text or "Model returned an empty response"

    @staticmethod
    def _finished(
        session_id: str,
        plan: TaskPlan,
        artifacts: RunArtifacts,
        status: TaskStatus,
        summary: str,
    ) -> AgentResponse:
        return AgentResponse(
            session_id=session_id,
            plan=plan,
            status=status,
            observations=list(artifacts.observations),
            summary=summary,
        )

    def _summarize_repository(self, target_root: Path) -> str:
        """Analyze and summarize the target repository (blocking; run in a thread)."""

        return self._repository_summarizer.summarize(self._repository_analyzer.analyze(target_root))


def _user_payload(task: str, artifacts: RunArtifacts, max_iterations: int) -> str:
    """Serialize the per-request context as one user message.

    The SDK's Session owns conversation history, so the snapshot carries the
    task, the bounded repository summary, the authorization flag, and the
    action budget the model is told about.
    """

    return json.dumps(
        {
            "task": task,
            "target_root": str(artifacts.target_root),
            "apply_changes": artifacts.apply_changes,
            "action_budget": {"limit": max_iterations},
            "repository_summary": artifacts.repository_summary,
        },
        indent=2,
        sort_keys=True,
    )
