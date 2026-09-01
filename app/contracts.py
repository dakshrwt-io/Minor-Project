"""Typed contracts shared across the gateway, planner, orchestrator, and tools.

Modeling style — pydantic vs dataclass: the rule is *boundaries*. Classes that
cross a trust or serialization boundary are pydantic BaseModel: gateway
request/response (JSON API), plans parsed from model output (untrusted, must
validate), and tool/test/external results (validated invariants such as
"success implies no error", plus serialization into the gateway response and
the prompt context). Classes that are trusted internal
value records — built by this codebase, never serialized for the API or
validated against external input — are plain frozen dataclasses instead:
`ExternalToolDefinition` here (an in-memory view of an MCP tool schema handed
to the prompt builder),
`TestCommand`/`TestRunResult` in testing/runner.py, the intelligence and MCP
config records. Both styles are immutable by design; the split is about
where validation and serialization pay for themselves, not about mutability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class FilesystemOperation(str, Enum):
    LIST = "list"
    READ = "read"
    CREATE = "create"
    WRITE = "write"
    EDIT = "edit"


class TaskStep(BaseModel):
    """One ordered, human-readable action in a task plan."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.PENDING


class TaskPlan(BaseModel):
    """The planner's ordered representation of a user request.

    `relevant_files` lists repository paths the planner grounded the plan in
    (from the repository summary), so the executing agent knows where to look
    first; it is advisory, not a constraint. `steps` is empty exactly when the
    router classified the message as conversation: the orchestrator then
    short-circuits and the reply becomes the response summary.
    """

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    steps: list[TaskStep] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    """The router's classification of one user message.

    Parsed from a forced `route_reply` tool call, so it is validated like
    every other model-shaped payload: a `chat` decision must carry a
    non-blank reply, and a `task` decision must carry none (the planner,
    not the router, words the work).
    """

    model_config = ConfigDict(frozen=True)

    route: Literal["chat", "task"]
    reply: str = ""

    @model_validator(mode="after")
    def validate_reply(self) -> RouteDecision:
        if self.route == "chat" and not self.reply.strip():
            raise ValueError("a chat decision requires a non-blank reply")
        if self.route == "task" and self.reply.strip():
            raise ValueError("a task decision must not carry a reply")
        return self


class ToolCall(BaseModel):
    """A requested, auditable call to a registered tool."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    operation: FilesystemOperation
    path: Path
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExternalToolCall(BaseModel):
    """A requested call to one advertised external MCP tool."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The observable outcome of a tool call."""

    model_config = ConfigDict(frozen=True)

    call: ToolCall
    succeeded: bool
    output: str = ""
    error: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> ToolResult:
        if self.succeeded and self.error is not None:
            raise ValueError("successful tool results cannot contain an error")
        if not self.succeeded and not self.error:
            raise ValueError("failed tool results must contain an error")
        return self


class TestResult(BaseModel):
    """The observable outcome of a configured target-repository test command."""

    model_config = ConfigDict(frozen=True)
    __test__ = False

    command: list[str]
    passed: bool
    output: str = ""
    return_code: int | None
    timed_out: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalToolDefinition:
    """A tool schema that can be exposed to the model without provider coupling."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ExternalToolResult(BaseModel):
    """An auditable result from one external-tool invocation."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    succeeded: bool
    content: tuple[str, ...] = ()
    structured_content: dict[str, Any] | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> ExternalToolResult:
        if self.succeeded and self.error is not None:
            raise ValueError("successful tool results cannot contain an error")
        if not self.succeeded and not self.error:
            raise ValueError("failed tool results must contain an error")
        return self


class AgentRequest(BaseModel):
    """Gateway input for a single coding-agent invocation.

    `session_id` is optional and client-owned: the interactive REPL mints one
    at startup and sends it with every message, so its conversation shares one
    session; when it is absent the gateway mints a fresh id (single-shot
    requests stay one-message-per-session). The id is only a memory key —
    history lives in the gateway's volatile session store.
    """

    task: str = Field(min_length=1)
    target_repo: Path
    apply_changes: bool = False
    session_id: str | None = Field(default=None, min_length=1)


class AgentResponse(BaseModel):
    """Gateway output containing the agent's plan and auditable observations."""

    session_id: str | None = None
    plan: TaskPlan
    status: TaskStatus
    observations: list[ToolResult | TestResult | ExternalToolResult] = Field(default_factory=list)
    summary: str = ""
