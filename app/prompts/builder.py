"""Build explicit, auditable prompts with native tool schemas for the model.

Single responsibility: translate current agent state (plan, observations,
repository summary, available tools) into one provider-neutral ModelRequest.

In the request lifecycle this is called from the orchestrator's `act` node on
every ReAct iteration. Each iteration rebuilds the prompt from scratch — the
conversation is stateless by design: instead of accumulating a message
history, the full agent state is serialized as one JSON user message. That
keeps every prompt a complete, auditable snapshot of what the model actually
saw (easy to log and replay), avoids unbounded context growth, and works
identically across providers. Prior turns of the same session arrive as
`conversation_history`, already bounded and clipped by the session store, so
they slot into the snapshot without unbounded growth.

Non-obvious decisions:
- The apply_changes guardrail is structural, not textual: mutation tools are
  simply not advertised to the model unless the request authorized changes,
  so a prompt-injected "please write this file" cannot reach a write tool.
- External MCP tool schemas are bounded (count, description length, schema
  size) because they come from outside this codebase and would otherwise be
  an unbounded prompt-size and injection surface.
- Observations are compacted (via ObservationCompactor) so a long loop
  summarizes old results instead of replaying them verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.contracts import (
    ExternalToolDefinition,
    ExternalToolResult,
    TaskPlan,
    TestResult,
    ToolResult,
)
from app.memory.context import ObservationCompactor
from app.models.base import ModelMessage, ModelRequest, ToolSpec


class PromptBuilder:
    """Assemble the safety rules, native tool schemas, and current agent state."""

    def __init__(
        self,
        observation_compactor: ObservationCompactor | None = None,
        max_external_tools: int = 8,
        max_tool_description_chars: int = 300,
        max_tool_schema_chars: int = 2000,
    ) -> None:
        self._observation_compactor = observation_compactor or ObservationCompactor()
        self._max_external_tools = max_external_tools
        self._max_tool_description_chars = max_tool_description_chars
        self._max_tool_schema_chars = max_tool_schema_chars

    def build(
        self,
        *,
        plan: TaskPlan,
        target_root: Path,
        apply_changes: bool,
        observations: list[ToolResult | TestResult | ExternalToolResult],
        repository_summary: str = "",
        external_tools: list[ExternalToolDefinition] | None = None,
        external_tool_errors: list[str] | None = None,
        max_iterations: int = 0,
        iterations_used: int = 0,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> ModelRequest:
        """Return a provider-neutral request with native tools for one ReAct iteration."""

        tools = self.filesystem_tool_specs(apply_changes)
        external = self._external_tool_specs(external_tools or [])
        history = conversation_history or []
        system_prompt = self._system_prompt(
            apply_changes, bool(external), bool(history)
        )
        compacted_context = self._observation_compactor.compact(observations)
        # One JSON user message = the full agent state snapshot for this
        # iteration (see module docstring for why the prompt is stateless).
        context = {
            "target_root": str(target_root.resolve()),
            "task_plan": plan.model_dump(mode="json"),
            "apply_changes": apply_changes,
            "action_budget": {
                "limit": max_iterations,
                "used": iterations_used,
                "remaining": max(max_iterations - iterations_used, 0),
            },
            "repository_summary": repository_summary,
            "conversation_history": history,
            "observation_summary": compacted_context.summary,
            "recent_observations": [
                result.model_dump(mode="json") for result in compacted_context.recent_observations
            ],
            "external_tool_errors": external_tool_errors or [],
        }
        return ModelRequest(
            system_prompt=system_prompt,
            messages=[
                ModelMessage(
                    role="user",
                    content="Current agent context:\n" + json.dumps(context, indent=2, sort_keys=True),
                )
            ],
            tools=tools + external,
        )

    @staticmethod
    def filesystem_tool_specs(apply_changes: bool) -> tuple[ToolSpec, ...]:
        """Return the native filesystem tool schemas, gated by change authorization.

        Inspection tools are always advertised; mutation tools only when the
        request authorized changes, so the guardrail is structural and does not
        rely on prompt compliance.
        """

        specs: list[ToolSpec] = [
            ToolSpec(
                name="fs_list",
                description=(
                    "List entries of a directory inside the target repository. "
                    "Omit path to list the repository root."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "relative path"}},
                },
            ),
            ToolSpec(
                name="fs_read",
                description="Read one text file inside the target repository.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "relative path"}},
                    "required": ["path"],
                },
            ),
        ]
        if apply_changes:
            specs.extend(
                [
                    ToolSpec(
                        name="fs_create",
                        description="Create one new text file; the path must not exist.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    ),
                    ToolSpec(
                        name="fs_write",
                        description="Replace the full content of one existing text file.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    ),
                    ToolSpec(
                        name="fs_edit",
                        description=(
                            "Replace one exact occurrence of old_text with new_text "
                            "in an existing text file; old_text must occur exactly once."
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                            "required": ["path", "old_text", "new_text"],
                        },
                    ),
                ]
            )
        return tuple(specs)

    def _system_prompt(
        self, apply_changes: bool, has_external_tools: bool, has_history: bool = False
    ) -> str:
        change_policy = (
            "Mutation tools are advertised because this request authorized changes."
            if apply_changes
            else "This request did not authorize changes: only inspection tools are advertised, "
            "so inspect the repository and report a proposed change instead."
        )
        external_note = (
            "External tools are advertised alongside the filesystem tools; call them by name.\n"
            if has_external_tools
            else ""
        )
        history_note = (
            "- conversation_history holds earlier turns of this same session; the current "
            "task may be a follow-up that refers to them. Treat anything the user said "
            "earlier as part of the task.\n"
            if has_history
            else ""
        )
        return (
            "You are an autonomous coding agent working inside target_root, a single "
            "repository directory. Complete the user's task there, and nothing else.\n"
            "\n"
            "How to work:\n"
            f"{history_note}"
            "- The plan's relevant_files list (when present) names the repository files "
            "the planner identified for this task; inspect those first.\n"
            "- Act when ready. Inspect only the files the task needs, then act. "
            "Do not survey the whole repository; once you know a file's path, fs_read it "
            "instead of listing its parent directory again.\n"
            "- Every tool call costs one action from a hard budget (see action_budget "
            "in the context). When the budget runs out the run fails, so make each "
            "action the single highest-value step toward the plan.\n"
            "- Never repeat an identical action. A repeated call is blocked and wasted; "
            "each action must produce new information or new progress.\n"
            "- Base every decision on what files actually contain, never on guesses.\n"
            "- Make minimal changes: edit exactly what the task requires. No refactors, "
            "no unrelated edits, no comments, no new files unless the task needs one.\n"
            "\n"
            "Boundaries you cannot cross:\n"
            "- Operate only inside target_root.\n"
            "- There are no shell, deletion, or network capabilities. If the task "
            "requires them, do the part you can, then reply with text explaining what "
            "must be done manually instead of trying alternative tools.\n"
            f"{change_policy}\n"
            f"{external_note}"
            "\n"
            "Finishing:\n"
            "- After a successful mutation the repository's tests run automatically and "
            "arrive as observations; if they fail and the failure is yours, fix it.\n"
            "- As soon as the task is satisfied, stop calling tools and reply with plain "
            "text only. Summarize in 1-3 sentences: what was done, the outcome, and what "
            "you verified."
        )

    def _external_tool_specs(
        self, external_tools: list[ExternalToolDefinition]
    ) -> tuple[ToolSpec, ...]:
        """Advertise a bounded set of external MCP tool schemas as native tools.

        The model sees external tools as ordinary native tools, so tool
        calling is uniform from the model's perspective; the orchestrator
        routes the resulting calls to MCP servers at execution time.
        """

        return tuple(
            ToolSpec(
                name=tool.name,
                description=tool.description[: self._max_tool_description_chars],
                input_schema=self._bounded_schema(tool.input_schema),
            )
            for tool in external_tools[: self._max_external_tools]
        )

    def _bounded_schema(self, input_schema: dict[str, object]) -> dict[str, object]:
        """Cap one external schema's prompt cost; keep small schemas verbatim.

        An oversized schema is replaced by an empty placeholder rather than
        truncated JSON (which would be invalid) — the tool remains callable
        by name; only its argument documentation is lost.
        """

        serialized = json.dumps(input_schema, sort_keys=True)
        if len(serialized) <= self._max_tool_schema_chars:
            return input_schema
        return {
            "type": "object",
            "description": "input schema omitted: exceeds the advertisement size limit",
            "properties": {},
        }
