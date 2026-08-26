"""State carried through the Phase 1 LangGraph ReAct loop."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from app.contracts import TaskPlan, TaskStatus, TestResult, ToolCall, ToolResult


class ReActState(TypedDict):
    task: str
    target_root: Path
    apply_changes: bool
    plan: TaskPlan | None
    observations: list[ToolResult | TestResult]
    pending_call: ToolCall | None
    summary: str
    status: TaskStatus | None
    iterations: int
