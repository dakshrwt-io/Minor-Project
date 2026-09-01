"""Task planning boundary: turn a natural-language request into a TaskPlan.

Single responsibility: produce the ordered TaskStep list the orchestrator's
ReAct loop executes. It runs once per request, before the loop starts, and is
the only planning stage — the plan is fixed for the rest of the request while
observations accumulate.

In the request lifecycle this sits between the gateway and the orchestrator:
the orchestrator's `plan` node calls create_plan() with the user's task, the
apply_changes flag (so the plan's final step matches what the agent is
actually allowed to do), and the repository summary from codebase
intelligence.

Two implementations, chosen at startup:
- ModelTaskPlanner asks the model to plan via a forced `submit_plan` tool
  call, so the reply arrives as validated JSON-shaped arguments rather than
  free text that would need fragile parsing. Unusable replies fall back to:
- DeterministicTaskPlanner, a fixed three-step plan (inspect → decide →
  complete) used for keyless demos and as that fallback.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import ValidationError

from app.contracts import TaskPlan, TaskStep
from app.models.base import (
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ToolSpec,
)

_SUBMIT_PLAN_TOOL = ToolSpec(
    name="submit_plan",
    description="Submit the ordered implementation plan for the user's task.",
    input_schema={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The restated goal of the task."},
            "relevant_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exact file paths from the repository summary that this plan "
                    "inspects or changes; empty when the summary names none."
                ),
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Short stable identifier for the step.",
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "One concrete, minimal action for this step, naming the "
                                "real file paths or symbols it targets whenever known."
                            ),
                        },
                    },
                    "required": ["id", "description"],
                },
            },
        },
        "required": ["goal", "steps"],
    },
)

_MAX_RELEVANT_FILES = 12


class TaskPlanner(ABC):
    """Turn a high-level coding request into ordered executable steps."""

    @abstractmethod
    async def create_plan(
        self,
        task: str,
        *,
        apply_changes: bool,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> TaskPlan:
        """Create a plan without inspecting or modifying the target repository.

        `conversation` holds earlier turns of the same session (as produced by
        the session store's payload), so follow-up tasks such as "now add
        tests for it" can be planned with their antecedent in view.
        """


class DeterministicTaskPlanner(TaskPlanner):
    """A predictable plan used for keyless demos and as the model-planner fallback."""

    async def create_plan(
        self,
        task: str,
        *,
        apply_changes: bool,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> TaskPlan:
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


class ModelTaskPlanner(TaskPlanner):
    """Plan with one model call, falling back when the reply is unusable.

    The model factory is invoked before any error handling, so configuration
    problems (for example a missing API key) propagate to the caller instead of
    being masked by the fallback. Only unusable plan content falls back.
    """

    def __init__(
        self,
        model_factory: Callable[[], ModelClient],
        fallback: TaskPlanner | None = None,
        max_plan_steps: int = 8,
    ) -> None:
        self._model_factory = model_factory
        self._fallback = fallback or DeterministicTaskPlanner()
        self._max_plan_steps = max_plan_steps

    async def create_plan(
        self,
        task: str,
        *,
        apply_changes: bool,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> TaskPlan:
        model = self._model_factory()
        request = ModelRequest(
            system_prompt=(
                "You are the task planner for an autonomous coding agent. Plan from "
                "evidence, never from guesses: the user message contains the task plus a "
                "repository_summary, an AST-derived index of the target repository listing "
                "its modules, top-level symbols, and import edges.\n"
                "First decide, from that summary, which real files and symbols the task "
                "actually touches; list those exact paths in relevant_files.\n"
                "conversation lists earlier turns of this session; use it when the task "
                "is a follow-up that refers to something said or done before.\n"
                "Then write a short ordered plan of concrete, minimal, safe steps a "
                "downstream agent can execute with filesystem tools only. Name the actual "
                "files or symbols in step descriptions instead of vague wording such as "
                "'relevant files'; the agent has no other knowledge of the codebase. "
                "Keep the plan within max_steps.\n"
                "Call the submit_plan tool exactly once; never reply with plain text."
            ),
            messages=[
                ModelMessage(
                    role="user",
                    # JSON payload (not prose) so the model sees a stable,
                    # unambiguous structure; sort_keys + indent keep the
                    # prompt byte-identical across runs for the same inputs,
                    # which makes tests and cached runs reproducible.
                    content=json.dumps(
                        {
                            "task": task,
                            "apply_changes": apply_changes,
                            "repository_summary": repository_summary,
                            "conversation": list(conversation),
                            "max_steps": self._max_plan_steps,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                )
            ],
            tools=(_SUBMIT_PLAN_TOOL,),
        )
        response = await model.complete(request)
        plan = self._parse_plan(response.tool_calls)
        if plan is None:
            return await self._fallback.create_plan(
                task,
                apply_changes=apply_changes,
                repository_summary=repository_summary,
                conversation=conversation,
            )
        return plan

    def _parse_plan(self, tool_calls: tuple[ModelToolCall, ...]) -> TaskPlan | None:
        """Validate the first submit_plan tool call; return None when unusable.

        Validation is all-or-nothing: if any step is malformed (or the model
        returned no submit_plan call at all), the whole plan is rejected and
        the caller falls back to the deterministic planner. A half-valid plan
        would be worse than a generic one — the agent would execute steps the
        model never actually justified.
        """

        payload: dict[str, Any] | None = next(
            (
                call.arguments
                for call in tool_calls
                if call.name == "submit_plan" and isinstance(call.arguments, dict)
            ),
            None,
        )
        if payload is None:
            return None
        goal = payload.get("goal")
        steps = payload.get("steps")
        if not isinstance(goal, str) or not goal.strip() or not isinstance(steps, list):
            return None
        relevant_files = self._parse_relevant_files(payload.get("relevant_files", []))
        if relevant_files is None:
            return None
        validated_steps: list[TaskStep] = []
        for step in steps[: self._max_plan_steps]:
            if not isinstance(step, dict):
                return None
            step_id = step.get("id")
            description = step.get("description")
            if not isinstance(step_id, str) or not isinstance(description, str):
                return None
            validated_steps.append(TaskStep(id=step_id, description=description))
        if not validated_steps:
            return None
        try:
            return TaskPlan(goal=goal, steps=validated_steps, relevant_files=relevant_files)
        except ValidationError:
            return None

    @staticmethod
    def _parse_relevant_files(value: Any) -> list[str] | None:
        """Validate the optional relevant_files list; None means unusable payload.

        Same all-or-nothing rule as steps: a malformed list rejects the whole
        plan instead of silently dropping evidence the model claimed.
        """

        if value is None:
            return []
        if not isinstance(value, list):
            return None
        files: list[str] = []
        for item in value[:_MAX_RELEVANT_FILES]:
            if not isinstance(item, str) or not item.strip():
                return None
            if item not in files:
                files.append(item)
        return files
