"""Typed contracts for the gateway API boundary and tool results.

Only types that cross a real boundary live here: the gateway request/response
models (JSON API), and the tool/test/external result records the response
serializes (validated invariants such as "success implies no error").
Internal orchestration shapes now belong to the OpenAI Agents SDK.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

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
    """The plan reported for a request.

    Planning is delegated to the agent's own tool-vs-text decisions, so the
    plan carries the restated goal; `steps` is populated only when an
    up-front structured plan is produced (the single agent does not force
    one), keeping the response shape stable for clients.
    """

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    steps: list[TaskStep] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    """A requested, auditable call to a filesystem operation."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    operation: FilesystemOperation
    path: Path
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


class ExternalToolResult(BaseModel):
    """An auditable result from one external-tool (MCP) invocation."""

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
    requests stay one-message-per-session). The id is the memory key for the
    session store backed by `AGENT_SESSION_DB`.
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
