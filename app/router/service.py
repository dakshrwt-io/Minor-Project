"""Triage boundary: decide whether a message is conversation or a coding task.

Single responsibility: run once per request, before the planner, and classify
the user message. Conversation (greetings, small talk, thanks, simple
questions about the repository) is answered directly; any actionable coding
request is handed off to the planner and the ReAct loop unchanged. This is
the same main-agent-then-handoff shape production coding agents use, and it
exists because a planner forced to produce steps will fabricate work from a
plain "hello" — the cheapest escape from "you must plan" is inventing a task.

In the request lifecycle this sits at the very front of the orchestrator:
message → router → (chat reply | planner → loop). The router never executes
tools and never sees the filesystem; its only output is a RouteDecision.

Two implementations, chosen at startup:
- ModelTaskRouter asks the model to classify via a forced `route_reply` tool
  call, so the decision arrives as validated JSON-shaped arguments rather
  than free text that would need fragile parsing. Unusable replies fall back:
- DeterministicTaskRouter, which routes everything to the planner. That is
  exactly the pre-router behavior, so fallback never misroutes and keyless
  scripted demos stay deterministic.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import ValidationError

from app.contracts import RouteDecision
from app.models.base import (
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ToolSpec,
)

_ROUTE_REPLY_TOOL = ToolSpec(
    name="route_reply",
    description="Classify the user message as conversation or as a coding task.",
    input_schema={
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "enum": ["chat", "task"],
                "description": "chat = conversational message; task = actionable coding request.",
            },
            "reply": {
                "type": "string",
                "description": (
                    "Brief conversational answer. Required when route is chat; "
                    "must be omitted when route is task."
                ),
            },
        },
        "required": ["route"],
    },
)


class TaskRouter(ABC):
    """Classify a user message as chat or as a coding task."""

    @abstractmethod
    async def route(
        self,
        task: str,
        *,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> RouteDecision:
        """Classify the message without inspecting or modifying the repository.

        `conversation` holds earlier turns of the same session (as produced by
        the session store's payload), so follow-up messages can be classified
        with their context instead of in isolation.
        """


class DeterministicTaskRouter(TaskRouter):
    """Route everything to the planner; used as the model-router fallback.

    This preserves the pre-router pipeline exactly: every message is treated
    as a task. A fallback that guesses chat on a heuristic would risk
    silently swallowing a real coding request, which is the worse failure.
    """

    async def route(
        self,
        task: str,
        *,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> RouteDecision:
        return RouteDecision(route="task")


class ModelTaskRouter(TaskRouter):
    """Classify with one model call, falling back when the reply is unusable.

    The model factory is invoked before any error handling, so configuration
    problems (for example a missing API key) propagate to the caller instead
    of being masked by the fallback. Only unusable classification content
    falls back — the same discipline as the model planner.
    """

    def __init__(
        self,
        model_factory: Callable[[], ModelClient],
        fallback: TaskRouter | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._fallback = fallback or DeterministicTaskRouter()

    async def route(
        self,
        task: str,
        *,
        repository_summary: str = "",
        conversation: Sequence[dict[str, str]] = (),
    ) -> RouteDecision:
        model = self._model_factory()
        request = ModelRequest(
            system_prompt=(
                "You are the triage router for a coding-agent gateway. Read the user "
                "message and classify it with the route_reply tool.\n"
                "- Greetings, small talk, thanks, identity questions, or simple "
                "questions about the repository: use route=chat and answer briefly "
                "and naturally in reply. repository_summary (an AST-derived index of "
                "the target repository) may inform answers about the codebase.\n"
                "- conversation lists earlier turns of this session; use it for "
                "follow-up messages that refer to something said before.\n"
                "- Any actionable coding request — build, fix, change, refactor, "
                "inspect for a purpose, explain then modify — use route=task with no "
                "reply. Never invent a coding task from a conversational message.\n"
                "Call the route_reply tool exactly once; never reply with plain text."
            ),
            messages=[
                ModelMessage(
                    role="user",
                    # JSON payload, sort_keys + indent: same reproducibility
                    # rationale as the planner's message encoding.
                    content=json.dumps(
                        {
                            "message": task,
                            "repository_summary": repository_summary,
                            "conversation": list(conversation),
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                )
            ],
            tools=(_ROUTE_REPLY_TOOL,),
        )
        response = await model.complete(request)
        decision = self._parse_decision(response.tool_calls)
        if decision is None:
            return await self._fallback.route(
                task, repository_summary=repository_summary, conversation=conversation
            )
        return decision

    def _parse_decision(self, tool_calls: tuple[ModelToolCall, ...]) -> RouteDecision | None:
        """Validate the first route_reply tool call; return None when unusable.

        All-or-nothing, like the planner: the RouteDecision validators enforce
        reply/repo-message discipline (chat needs a reply, task forbids one),
        and any violation rejects the whole decision so the deterministic
        fallback takes over.
        """

        payload: dict[str, Any] | None = next(
            (
                call.arguments
                for call in tool_calls
                if call.name == "route_reply" and isinstance(call.arguments, dict)
            ),
            None,
        )
        if payload is None:
            return None
        try:
            return RouteDecision.model_validate(payload)
        except ValidationError:
            return None
