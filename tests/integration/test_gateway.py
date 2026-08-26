from collections.abc import Iterable
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ModelProvider, Settings
from app.main import create_app
from app.models.base import ModelClient, ModelRequest, ModelResponse
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder


class SequencedFakeModel(ModelClient):
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=next(self._responses), model_name="fake")


def test_gateway_runs_agent_against_explicit_target_repository(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Gateway demo", encoding="utf-8")
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {
            ModelProvider.ANTHROPIC: lambda _: SequencedFakeModel(
                [
                    '{"kind":"tool_call","tool_name":"filesystem","operation":"read",'
                    '"path":"README.md","arguments":{}}',
                    '{"kind":"final","summary":"README reviewed."}',
                ]
            )
        },
    )
    orchestrator = ReActOrchestrator(
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=2,
    )
    client = TestClient(create_app(orchestrator))

    response = client.post(
        "/v1/agent/run",
        json={
            "task": "Review the README",
            "target_repo": str(tmp_path),
            "apply_changes": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["summary"] == "README reviewed."
    assert body["observations"][0]["output"] == "Gateway demo"
