import json
from collections.abc import Iterable
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ModelProvider, Settings
from app.main import create_app
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder
from app.router.service import DeterministicTaskRouter, ModelTaskRouter


class SequencedFakeModel(ModelClient):
    def __init__(self, responses: Iterable[ModelResponse]) -> None:
        self._responses = iter(responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return next(self._responses)


def test_gateway_runs_agent_against_explicit_target_repository(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Gateway demo", encoding="utf-8")
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {
            ModelProvider.ANTHROPIC: lambda _: SequencedFakeModel(
                [
                    ModelResponse(
                        text="",
                        model_name="fake",
                        tool_calls=(
                            ModelToolCall(id="t1", name="fs_read", arguments={"path": "README.md"}),
                        ),
                    ),
                    ModelResponse(text="README reviewed.", model_name="fake"),
                ]
            )
        },
    )
    orchestrator = ReActOrchestrator(
        task_router=DeterministicTaskRouter(),
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


def test_gateway_stream_endpoint_emits_live_sse_events(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Gateway demo", encoding="utf-8")
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {
            ModelProvider.ANTHROPIC: lambda _: SequencedFakeModel(
                [
                    ModelResponse(
                        text="",
                        model_name="fake",
                        tool_calls=(
                            ModelToolCall(id="t1", name="fs_read", arguments={"path": "README.md"}),
                        ),
                    ),
                    ModelResponse(text="README reviewed.", model_name="fake"),
                ]
            )
        },
    )
    orchestrator = ReActOrchestrator(
        task_router=DeterministicTaskRouter(),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=2,
    )
    client = TestClient(create_app(orchestrator))

    with client.stream(
        "POST",
        "/v1/agent/run/stream",
        json={
            "task": "Review the README",
            "target_repo": str(tmp_path),
            "apply_changes": False,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [event["type"] for event in events] == ["plan", "action", "observation", "done"]
    assert events[1]["name"] == "fs_read"
    assert events[2]["observation"]["output"] == "Gateway demo"
    done = events[-1]
    assert done["status"] == "completed"
    assert done["summary"] == "README reviewed."
    assert done["response"]["observations"][0]["output"] == "Gateway demo"


class RouteReplyModel(ModelClient):
    """Fake triage model that always answers with one route_reply call."""

    def __init__(self, arguments: dict) -> None:
        self._arguments = arguments

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="",
            model_name="fake",
            tool_calls=(ModelToolCall(id="r1", name="route_reply", arguments=self._arguments),),
        )


def test_gateway_stream_answers_chat_without_tool_events(tmp_path: Path) -> None:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {
            ModelProvider.ANTHROPIC: lambda _: RouteReplyModel(
                {"route": "chat", "reply": "Hey! What should we build?"}
            )
        },
    )
    orchestrator = ReActOrchestrator(
        task_router=ModelTaskRouter(router.get_model),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=2,
    )
    client = TestClient(create_app(orchestrator))

    with client.stream(
        "POST",
        "/v1/agent/run/stream",
        json={"task": "hello", "target_repo": str(tmp_path), "apply_changes": False},
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [event["type"] for event in events] == ["plan", "done"]
    assert events[0]["plan"]["steps"] == []
    done = events[-1]
    assert done["status"] == "completed"
    assert done["summary"] == "Hey! What should we build?"
    assert done["response"]["observations"] == []


def test_gateway_keeps_conversation_history_across_requests_in_one_session(tmp_path: Path) -> None:
    class SessionAwareRouteModel(RouteReplyModel):
        """Record the conversation payload each classification request carried."""

        def __init__(self) -> None:
            super().__init__({"route": "chat", "reply": "Hello!"})
            self.conversations: list[list[dict]] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            payload = json.loads(request.messages[0].content)
            self.conversations.append(payload["conversation"])
            return await super().complete(request)

    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    model = SessionAwareRouteModel()
    router = ModelRouter(settings, {ModelProvider.ANTHROPIC: lambda _: model})
    orchestrator = ReActOrchestrator(
        task_router=ModelTaskRouter(router.get_model),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=2,
    )
    client = TestClient(create_app(orchestrator))
    body = {"task": "hello", "target_repo": str(tmp_path), "apply_changes": False}

    first = client.post("/v1/agent/run", json={**body, "session_id": "repl-42"})
    second = client.post("/v1/agent/run", json={**body, "task": "what is my name"})

    assert first.status_code == 200
    assert first.json()["session_id"] == "repl-42"
    assert second.status_code == 200
    assert second.json()["session_id"] != "repl-42"  # no id supplied: fresh session
    assert model.conversations[0] == []
    assert model.conversations[1] == []  # a different session sees no other session's history


def test_gateway_same_session_sees_prior_turns(tmp_path: Path) -> None:
    class SessionAwareRouteModel(RouteReplyModel):
        def __init__(self) -> None:
            super().__init__({"route": "chat", "reply": "Hello!"})
            self.conversations: list[list[dict]] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            payload = json.loads(request.messages[0].content)
            self.conversations.append(payload["conversation"])
            return await super().complete(request)

    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    model = SessionAwareRouteModel()
    router = ModelRouter(settings, {ModelProvider.ANTHROPIC: lambda _: model})
    orchestrator = ReActOrchestrator(
        task_router=ModelTaskRouter(router.get_model),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=2,
    )
    client = TestClient(create_app(orchestrator))

    client.post(
        "/v1/agent/run",
        json={"task": "hello", "target_repo": str(tmp_path), "session_id": "repl-7"},
    )
    client.post(
        "/v1/agent/run",
        json={"task": "again", "target_repo": str(tmp_path), "session_id": "repl-7"},
    )

    assert model.conversations[0] == []
    assert model.conversations[1] == [
        {"user": "hello", "agent": "Hello!", "route": "chat"}
    ]
