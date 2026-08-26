"""Build explicit, auditable Phase 1 prompts for the language model."""

from __future__ import annotations

import json
from pathlib import Path

from app.contracts import TaskPlan, TestResult, ToolResult
from app.memory.context import ObservationCompactor
from app.models.base import ModelMessage, ModelRequest


class PromptBuilder:
    """Assemble the fixed safety rules, tool schema, and current agent state."""

    def __init__(self, observation_compactor: ObservationCompactor | None = None) -> None:
        self._observation_compactor = observation_compactor or ObservationCompactor()

    def build(
        self,
        *,
        plan: TaskPlan,
        target_root: Path,
        apply_changes: bool,
        observations: list[ToolResult | TestResult],
    ) -> ModelRequest:
        """Return a provider-neutral request for one ReAct iteration."""

        system_prompt = self._system_prompt(apply_changes)
        compacted_context = self._observation_compactor.compact(observations)
        context = {
            "target_root": str(target_root.resolve()),
            "task_plan": plan.model_dump(mode="json"),
            "apply_changes": apply_changes,
            "observation_summary": compacted_context.summary,
            "recent_observations": [
                result.model_dump(mode="json") for result in compacted_context.recent_observations
            ],
        }
        return ModelRequest(
            system_prompt=system_prompt,
            messages=[
                ModelMessage(
                    role="user",
                    content="Current agent context:\n" + json.dumps(context, indent=2, sort_keys=True),
                )
            ],
        )

    @staticmethod
    def filesystem_schema() -> dict[str, object]:
        """Return the only tool schema exposed in the Phase 1 prompt."""

        return {
            "name": "filesystem",
            "description": "Read and make confined text-file changes within target_root.",
            "operations": {
                "list": {"arguments": {}},
                "read": {"arguments": {}},
                "create": {"arguments": {"content": "string"}},
                "write": {"arguments": {"content": "string"}},
                "edit": {"arguments": {"old_text": "string", "new_text": "string"}},
            },
        }

    def _system_prompt(self, apply_changes: bool) -> str:
        change_policy = (
            "You may select create, write, or edit when the plan requires it."
            if apply_changes
            else "Do not select create, write, or edit; inspect and report a proposed change instead."
        )
        tool_schema = json.dumps(self.filesystem_schema(), indent=2, sort_keys=True)
        return (
            "You are a coding agent operating only inside the supplied target_root.\n"
            "Work on one safe, minimal next action. Never request shell commands or deletion.\n"
            f"{change_policy}\n"
            "Return exactly one JSON object in one of these forms:\n"
            '{"kind":"tool_call","tool_name":"filesystem","operation":"read",'
            '"path":"relative/path","arguments":{}}\n'
            'or {"kind":"final","summary":"concise result"}.\n'
            "Use only the following tool schema:\n"
            f"{tool_schema}"
        )
