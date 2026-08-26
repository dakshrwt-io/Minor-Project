"""Task planning boundary for coding-agent requests."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.contracts import TaskPlan, TaskStep


class TaskPlanner(ABC):
    """Turn a high-level coding request into ordered executable steps."""

    @abstractmethod
    def create_plan(self, task: str, *, apply_changes: bool) -> TaskPlan:
        """Create a plan without inspecting or modifying the target repository."""


class DeterministicTaskPlanner(TaskPlanner):
    """A predictable Phase 1 plan used before model-driven planning is needed."""

    def create_plan(self, task: str, *, apply_changes: bool) -> TaskPlan:
        final_step = (
            "Apply the smallest filesystem change that satisfies the request."
            if apply_changes
            else "Describe the smallest filesystem change that would satisfy the request."
        )
        return TaskPlan(
            goal=task,
            steps=[
                TaskStep(
                    id="inspect",
                    description=f"Inspect target-repository files relevant to: {task}",
                ),
                TaskStep(
                    id="decide",
                    description=f"Determine a minimal, safe change for: {task}",
                ),
                TaskStep(id="complete", description=final_step),
            ],
        )
