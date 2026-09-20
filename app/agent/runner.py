"""The AgentRunner: one request driven through `Runner.run_streamed()`."""

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
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.items import ItemHelpers, ToolCallItem, ToolCallOutputItem
from agents.stream_events import RunItemStreamEvent

from app.agent.mcp import BoundedMCPServer, _mcp_failure_message, build_mcp_server
from app.agent.observations import _decode_tool_output, _raw_arguments
from app.agent.prompts import SYSTEM_INSTRUCTIONS_TEMPLATE, build_model
from app.agent.session import BoundedSession
from app.agent.tools import (
    _FILESYSTEM_TOOL_OPERATIONS,
    _REPEAT_BLOCKED_ERROR,
    _external_payload,
    _filesystem_payload,
    filesystem_tools,
    test_tool,
)
from app.config import Settings
from app.contracts import (
    AgentRequest,
    AgentResponse,
    ExternalToolResult,
    TaskPlan,
    TaskStatus,
    TestResult,
    ToolCall,
    ToolResult,
)
from app.intelligence.python_analyzer import PythonProjectAnalyzer
from app.intelligence.summary import PythonProjectSummarizer

# Tracing exports would leave the process; this service stays self-contained.
set_tracing_disabled(True)

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]


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
