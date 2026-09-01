"""Keyless demo gateway: a fake model drives a scripted two-scenario ReAct run.

No Anthropic API key is required. Use for the demo walkthrough only.

Scenario selection (environment variable):
  FAKE_MODEL_SCENARIO=basic (default) — fix greeting.py until its test passes.
  FAKE_MODEL_SCENARIO=mcp          — call demo.echo through the MCP server.

Launch from the repository root:  python examples/fake_model_app.py
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn

from app.config import ModelProvider, Settings
from app.main import create_app
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder
from app.router.service import DeterministicTaskRouter


def _tool(name: str, arguments: dict | None = None) -> ModelResponse:
    return ModelResponse(
        text="",
        model_name="fake",
        tool_calls=(ModelToolCall(id="t1", name=name, arguments=arguments or {}),),
    )


def _final(text: str) -> ModelResponse:
    return ModelResponse(text=text, model_name="fake")


BASIC_SCENARIO = [
    _tool("fs_read", {"path": "greeting.py"}),
    _tool("fs_write", {"path": "greeting.py", "content": 'def greet():\n    return "Hello"'}),
    _tool("fs_write", {"path": "greeting.py", "content": 'def greet():\n    return "Hello, World!"'}),
    _final("greeting.py now returns the expected greeting."),
]

MCP_SCENARIO = [
    _tool("demo.echo", {"text": "hello from the agent"}),
    _final("The external echo tool returned the message."),
]


class ScriptedFakeModel(ModelClient):
    """Return canned model decisions in order, then fail loudly if exhausted."""

    def __init__(self, responses: Iterable[ModelResponse]) -> None:
        self._responses = iter(responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return next(self._responses)


def main() -> None:
    scenario = os.environ.get("FAKE_MODEL_SCENARIO", "basic")
    responses = MCP_SCENARIO if scenario == "mcp" else BASIC_SCENARIO
    settings = Settings.from_env()
    router = ModelRouter(
        settings, {ModelProvider.ANTHROPIC: lambda _: ScriptedFakeModel(responses)}
    )
    orchestrator = ReActOrchestrator(
        # Deterministic triage: the scripted responses feed the planner/loop,
        # so the demo transcript must not spend one on classification.
        task_router=DeterministicTaskRouter(),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=6,
        mcp_servers=list(settings.mcp_servers),
    )
    uvicorn.run(create_app(orchestrator), host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
