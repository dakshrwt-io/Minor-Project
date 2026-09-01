import asyncio

import pytest
from pydantic import ValidationError

from app.contracts import RouteDecision
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.router.service import DeterministicTaskRouter, ModelTaskRouter


def test_deterministic_router_sends_everything_to_the_planner() -> None:
    decision = asyncio.run(DeterministicTaskRouter().route("hello", repository_summary="s"))

    assert decision.route == "task"
    assert decision.reply == ""


class StaticFakeModel(ModelClient):
    def __init__(self, response: ModelResponse) -> None:
        self._response = response

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return self._response


class FailingModel(ModelClient):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("provider request failed")


class RecordingFallback(DeterministicTaskRouter):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def route(
        self, task: str, *, repository_summary: str = "", conversation=()
    ) -> RouteDecision:
        self.calls.append(
            {"task": task, "repository_summary": repository_summary, "conversation": conversation}
        )
        return await super().route(
            task, repository_summary=repository_summary, conversation=conversation
        )


def _route_reply_response(arguments: dict) -> ModelResponse:
    return ModelResponse(
        text="",
        model_name="fake",
        tool_calls=(ModelToolCall(id="t1", name="route_reply", arguments=arguments),),
    )


def test_model_router_classifies_chat_with_a_reply() -> None:
    model = StaticFakeModel(
        _route_reply_response({"route": "chat", "reply": "Hey! What should we build?"})
    )

    decision = asyncio.run(ModelTaskRouter(lambda: model).route("hello"))

    assert decision.route == "chat"
    assert decision.reply == "Hey! What should we build?"


def test_model_router_hands_coding_tasks_to_the_planner() -> None:
    model = StaticFakeModel(_route_reply_response({"route": "task"}))

    decision = asyncio.run(ModelTaskRouter(lambda: model).route("Fix the failing test"))

    assert decision.route == "task"
    assert decision.reply == ""


def test_model_router_sends_message_and_summary_to_the_model() -> None:
    captured: list[ModelRequest] = []

    class CapturingModel(StaticFakeModel):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return await super().complete(request)

    model = CapturingModel(_route_reply_response({"route": "task"}))

    asyncio.run(
        ModelTaskRouter(lambda: model).route("hello", repository_summary="repo summary")
    )

    assert [tool.name for tool in captured[0].tools] == ["route_reply"]
    assert '"message": "hello"' in captured[0].messages[0].content
    assert "repo summary" in captured[0].messages[0].content
    assert '"conversation": []' in captured[0].messages[0].content
    assert "route=chat" in captured[0].system_prompt
    assert "Never invent a coding task" in captured[0].system_prompt


def test_model_router_passes_prior_conversation_turns_to_the_model() -> None:
    captured: list[ModelRequest] = []

    class CapturingModel(StaticFakeModel):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return await super().complete(request)

    model = CapturingModel(_route_reply_response({"route": "chat", "reply": "Daksh"}))
    history = [{"user": "my name is Daksh", "agent": "Nice to meet you!", "route": "chat"}]

    decision = asyncio.run(
        ModelTaskRouter(lambda: model).route("what is my name", conversation=history)
    )

    assert decision.reply == "Daksh"
    assert '"user": "my name is Daksh"' in captured[0].messages[0].content
    assert "conversation lists earlier turns" in captured[0].system_prompt


def test_model_router_falls_back_when_the_model_replies_without_a_route() -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(ModelResponse(text="Hello! How can I help?", model_name="fake"))

    decision = asyncio.run(ModelTaskRouter(lambda: model, fallback).route("hello"))

    assert fallback.calls == [{"task": "hello", "repository_summary": "", "conversation": ()}]
    assert decision.route == "task"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"route": "maybe"},
        {"route": "chat"},
        {"route": "chat", "reply": "   "},
        {"route": "task", "reply": "I will fix it"},
        {"route": 7},
    ],
)
def test_model_router_falls_back_on_unusable_route_reply(arguments: dict) -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(_route_reply_response(arguments))

    decision = asyncio.run(ModelTaskRouter(lambda: model, fallback).route("hello"))

    assert len(fallback.calls) == 1
    assert decision.route == "task"


def test_model_router_propagates_provider_configuration_errors() -> None:
    with pytest.raises(RuntimeError):
        asyncio.run(ModelTaskRouter(FailingModel).route("hello"))


def test_route_decision_rejects_impossible_shapes() -> None:
    with pytest.raises(ValidationError):
        RouteDecision(route="chat")
    with pytest.raises(ValidationError):
        RouteDecision(route="task", reply="on my way")
    with pytest.raises(ValidationError):
        RouteDecision(route="nonsense", reply="x")
