"""Bounded Plan → Act → Observe loop that executes one agent request.

The ReActOrchestrator is the control-flow engine of the system: the gateway
hands it an AgentRequest, it prepares per-request state (repository summary,
session record, filesystem tool, external MCP tools), then runs the triage
router once and drives a plain async loop until the model produces a final
answer or the action budget runs out:

    route (once) → ──chat──→ COMPLETED (the reply is the summary)
                   └─task→ plan (once) → act → observe → act → …
                                ├─(budget gone)──→ FAILED (limit)
                                └─(model answered in text)→ COMPLETED

Every other component (planner, prompt builder, model router, filesystem
tool, test runner, MCP adapters) is invoked from inside the loop; this module
owns only the loop, the iteration cap, and the mapping from model-issued tool
names to internal calls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.contracts import (
    AgentRequest,
    AgentResponse,
    ExternalToolCall,
    ExternalToolDefinition,
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
from app.mcp.adapter import McpClientAdapter
from app.mcp.connection import (
    McpDiscovery,
    McpServerConfig,
    McpServerConnection,
    open_mcp_servers,
)
from app.memory.session import ConversationTurn, SessionStore
from app.models.base import ModelToolCall
from app.models.router import ModelRouter
from app.planner.service import TaskPlanner
from app.prompts.builder import PromptBuilder
from app.router.service import TaskRouter
from app.testing.runner import TestRunner
from app.tools.filesystem import FilesystemTool

# Filesystem tool names the model may call, mapped to the operation each name
# performs. Any tool name outside this table is treated as an external (MCP)
# tool call instead.
_FILESYSTEM_TOOLS: dict[str, FilesystemOperation] = {
    "fs_list": FilesystemOperation.LIST,
    "fs_read": FilesystemOperation.READ,
    "fs_create": FilesystemOperation.CREATE,
    "fs_write": FilesystemOperation.WRITE,
    "fs_edit": FilesystemOperation.EDIT,
}

# File operations that change the repository. After any of these succeeds, the
# loop runs the repo's configured test suite so the model sees the pass/fail
# outcome on its next turn, not just the file result.
_MUTATING_OPERATIONS = frozenset(
    {FilesystemOperation.CREATE, FilesystemOperation.WRITE, FilesystemOperation.EDIT}
)

# Callback that receives one live-progress event dict as it happens (plan,
# action, observation). Optional; the streaming gateway route supplies one.
ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]

# Error text appended as a failed observation when the model repeats an
# identical action back to back: the loop must not burn its action budget
# re-doing the same call, and the model must be told why.
_REPEAT_BLOCKED_ERROR = (
    "blocked: identical to the previous action. Choose a different action that "
    "advances the task, or reply with plain text to finish."
)


def _to_pending_call(tool_call: ModelToolCall) -> ToolCall | ExternalToolCall:
    """Map one model-issued native tool call to an internal pending call.

    Filesystem tools map to a ToolCall with their operation; every other name
    becomes an ExternalToolCall and is rejected at the observation boundary if
    it was not advertised, which keeps unknown names auditable.
    """

    operation = _FILESYSTEM_TOOLS.get(tool_call.name)
    if operation is None:
        return ExternalToolCall(tool_name=tool_call.name, arguments=dict(tool_call.arguments))
    arguments = {key: value for key, value in tool_call.arguments.items() if key != "path"}
    path = tool_call.arguments.get("path", ".")
    if not isinstance(path, str) or not path:
        path = "."
    return ToolCall(
        tool_name="filesystem",
        operation=operation,
        path=Path(path),
        arguments=arguments,
    )


def _call_signature(tool_call: ModelToolCall) -> tuple[str, str]:
    """Canonical identity of a model-issued call, used to detect repetition."""

    arguments = json.dumps(tool_call.arguments, sort_keys=True, default=str)
    return (tool_call.name, arguments)


class ReActOrchestrator:
    """Run a request through a bounded Plan → Act → Observe loop."""

    def __init__(
        self,
        *,
        task_router: TaskRouter,
        planner: TaskPlanner,
        model_router: ModelRouter,
        prompt_builder: PromptBuilder,
        max_iterations: int,
        repository_analyzer: PythonProjectAnalyzer | None = None,
        repository_summarizer: PythonProjectSummarizer | None = None,
        session_store: SessionStore | None = None,
        mcp_servers: Sequence[McpServerConfig] = (),
        external_tool_opener: Callable[
            [Sequence[McpServerConfig]], AbstractAsyncContextManager[McpDiscovery]
        ] = open_mcp_servers,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self._task_router = task_router
        self._planner = planner
        self._model_router = model_router
        self._prompt_builder = prompt_builder
        self._max_iterations = max_iterations
        self._repository_analyzer = repository_analyzer or PythonProjectAnalyzer()
        self._repository_summarizer = repository_summarizer or PythonProjectSummarizer()
        self._session_store = session_store or SessionStore()
        self._mcp_servers = mcp_servers
        self._external_tool_opener = external_tool_opener or open_mcp_servers

    async def run(
        self, request: AgentRequest, emit: ProgressEmitter | None = None
    ) -> AgentResponse:
        """Execute one agent request against its explicitly supplied target root.

        The session id comes from the request when the client supplies one (the
        interactive REPL reuses its id across messages, so its conversation
        accumulates in the volatile session store and later turns can reference
        earlier ones); otherwise a fresh id is minted, keeping single-shot
        requests one-message-per-session. Nothing is persisted: the store dies
        with the gateway process. When `emit` is supplied it is awaited with a
        progress event dict at every plan/action/observation step, so streaming
        transports can show the run in real time.
        """

        async def notify(event: dict[str, Any]) -> None:
            if emit is not None:
                await emit(event)

        target_root = request.target_repo.resolve()
        session_id = request.session_id or str(uuid4())
        repository_summary = await asyncio.to_thread(self._summarize_repository, target_root)
        # History recorded so far, before this turn is appended: the router,
        # planner, and loop see prior turns only, so the current message is
        # never duplicated in the context.
        conversation = self._session_store.payload(session_id)

        # Triage before any tool or model-loop state exists: a conversational
        # message is answered directly and never reaches the planner or the
        # filesystem, so the cheapest escape from "you must plan" cannot be
        # inventing a task from "hello".
        decision = await self._task_router.route(
            request.task,
            repository_summary=repository_summary,
            conversation=conversation,
        )
        if decision.route == "chat":
            chat_plan = TaskPlan(goal=request.task)
            await notify(
                {
                    "type": "plan",
                    "session_id": session_id,
                    "plan": chat_plan.model_dump(mode="json"),
                }
            )
            self._session_store.record(
                session_id,
                ConversationTurn(message=request.task, reply=decision.reply, route="chat"),
            )
            return AgentResponse(
                session_id=session_id,
                plan=chat_plan,
                status=TaskStatus.COMPLETED,
                summary=decision.reply,
            )

        tool = FilesystemTool(target_root, allow_changes=request.apply_changes)
        model = self._model_router.get_model()

        async with self._external_tool_opener(self._mcp_servers) as discovery:
            external_tool_map = await self._map_external_tools(discovery.connections)
            plan = await self._planner.create_plan(
                request.task,
                apply_changes=request.apply_changes,
                repository_summary=repository_summary,
                conversation=conversation,
            )
            await notify(
                {
                    "type": "plan",
                    "session_id": session_id,
                    "plan": plan.model_dump(mode="json"),
                }
            )
            observations: list[ToolResult | TestResult | ExternalToolResult] = []
            iterations = 0
            last_signature: tuple[str, str] | None = None
            empty_streak = 0
            while True:
                prompt = self._prompt_builder.build(
                    plan=plan,
                    target_root=target_root,
                    apply_changes=request.apply_changes,
                    observations=observations,
                    repository_summary=repository_summary,
                    external_tools=[
                        definition for _, definition in external_tool_map.values()
                    ],
                    external_tool_errors=discovery.errors,
                    max_iterations=self._max_iterations,
                    iterations_used=iterations,
                    conversation_history=conversation,
                )
                response = await model.complete(prompt)
                if not response.tool_calls and not response.text.strip():
                    # Some providers intermittently return a truncated, empty
                    # body (e.g. a reasoning model that spends its whole token
                    # budget before answering). One bounded retry — the same
                    # prompt, a fresh draw — recovers the transient case; a
                    # second consecutive empty reply fails the run instead of
                    # spinning. The streak never grows past 1, so this cannot
                    # loop forever.
                    if empty_streak >= 1:
                        status = TaskStatus.FAILED
                        summary = "Model returned an empty response"
                        break
                    empty_streak = 1
                    continue
                empty_streak = 0
                if response.tool_calls:
                    tool_call = response.tool_calls[0]
                    pending_call = _to_pending_call(tool_call)
                    await notify(
                        {
                            "type": "action",
                            "name": tool_call.name,
                            "arguments": dict(tool_call.arguments),
                        }
                    )
                    signature = _call_signature(tool_call)
                    if signature == last_signature:
                        new_observations: list[ToolResult | TestResult | ExternalToolResult] = [
                            self._repeated_action_result(pending_call)
                        ]
                    elif isinstance(pending_call, ExternalToolCall):
                        new_observations = [
                            await self._call_external(pending_call, external_tool_map)
                        ]
                    else:
                        # Filesystem calls are blocking; run them in a worker thread.
                        new_observations = [await asyncio.to_thread(tool.execute, pending_call)]
                        # After a successful file mutation, immediately run the
                        # repo's configured test suite (if any) so the model
                        # sees the pass/fail outcome on its next act turn.
                        if (
                            new_observations[0].succeeded
                            and pending_call.operation in _MUTATING_OPERATIONS
                        ):
                            test_result = await asyncio.to_thread(
                                self._run_configured_tests, target_root
                            )
                            if test_result is not None:
                                new_observations.append(test_result)
                    last_signature = signature
                    observations.extend(new_observations)
                    iterations += 1
                    for observation in new_observations:
                        await notify(
                            {
                                "type": "observation",
                                "observation": observation.model_dump(mode="json"),
                                "iteration": iterations,
                                "remaining": max(self._max_iterations - iterations, 0),
                            }
                        )
                    if iterations >= self._max_iterations:
                        status = TaskStatus.FAILED
                        summary = (
                            f"Stopped after reaching the {self._max_iterations}-action limit."
                        )
                        break
                    continue
                final_summary = response.text.strip()
                status = TaskStatus.COMPLETED
                summary = final_summary
                break

        agent_response = AgentResponse(
            session_id=session_id,
            plan=plan,
            status=status,
            observations=observations,
            summary=summary,
        )
        # Record the finished turn (success or failure) so later messages in
        # the same session can refer back to what was asked and what happened.
        self._session_store.record(
            session_id, ConversationTurn(message=request.task, reply=summary, route="task")
        )
        return agent_response

    @staticmethod
    def _repeated_action_result(
        pending_call: ToolCall | ExternalToolCall,
    ) -> ToolResult | ExternalToolResult:
        """Build the auditable failed observation for a blocked repeated action."""

        if isinstance(pending_call, ExternalToolCall):
            return ExternalToolResult(
                tool_name=pending_call.tool_name, succeeded=False, error=_REPEAT_BLOCKED_ERROR
            )
        return ToolResult(call=pending_call, succeeded=False, error=_REPEAT_BLOCKED_ERROR)

    def _summarize_repository(self, target_root: Path) -> str:
        """Analyze and summarize the target repository (blocking; run in a thread)."""

        return self._repository_summarizer.summarize(
            self._repository_analyzer.analyze(target_root)
        )

    @staticmethod
    async def _map_external_tools(
        connections: Sequence[McpServerConnection],
    ) -> dict[str, tuple[McpClientAdapter, ExternalToolDefinition]]:
        """Route each advertised (server-qualified) tool name to its live adapter.

        Qualification by server name makes collisions impossible across servers.
        Only tools collected from a live connection are callable: an unknown
        name is rejected at the observation boundary, which is the guardrail
        for model-issued external calls.
        """

        tool_map: dict[str, tuple[McpClientAdapter, ExternalToolDefinition]] = {}
        for connection in connections:
            for tool in await connection.adapter.list_tools():
                tool_map[tool.name] = (connection.adapter, tool)
        return tool_map

    @staticmethod
    async def _call_external(
        call: ExternalToolCall,
        external_tool_map: dict[str, tuple[McpClientAdapter, ExternalToolDefinition]],
    ) -> ExternalToolResult:
        """Call one advertised external tool, or reject unknown tool names."""

        entry = external_tool_map.get(call.tool_name)
        if entry is None:
            return ExternalToolResult(
                tool_name=call.tool_name,
                succeeded=False,
                error=f"unknown external tool '{call.tool_name}' is not advertised",
            )
        adapter, _ = entry
        return await adapter.call_tool(call.tool_name, call.arguments)

    @staticmethod
    def _run_configured_tests(target_root: Path) -> TestResult | None:
        """Run the repository's opt-in test command after a successful mutation.

        Blocking subprocess execution; the loop runs it in a worker thread.
        """

        try:
            command = TestRunner.discover(target_root)
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
