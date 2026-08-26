"""Typed contracts shared across the gateway, planner, orchestrator, and tools."""

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
    """The planner's ordered representation of a user request."""

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    steps: list[TaskStep] = Field(min_length=1)


class ToolCall(BaseModel):
    """A requested, auditable call to a registered tool."""

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


class AgentRequest(BaseModel):
    """Gateway input for a single coding-agent invocation."""

    task: str = Field(min_length=1)
    target_repo: Path
    apply_changes: bool = False


class AgentResponse(BaseModel):
    """Gateway output containing the agent's plan and auditable observations."""

    session_id: str | None = None
    plan: TaskPlan
    status: TaskStatus
    observations: list[ToolResult | TestResult] = Field(default_factory=list)
    summary: str = ""
