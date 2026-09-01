import asyncio
import sys
import types

import pytest

from app.config import ModelProvider, Settings
from app.models.anthropic import AnthropicModel
from app.models.base import (
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolSpec,
)
from app.models.deepseek import DeepSeekModel
from app.models.router import ModelRouter


class FakeModel(ModelClient):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="fake response", model_name="fake")


def test_router_uses_registered_factory() -> None:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(settings, {ModelProvider.ANTHROPIC: lambda _: FakeModel()})

    assert isinstance(router.get_model(), FakeModel)


def test_anthropic_model_requires_an_api_key() -> None:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicModel.from_settings(settings)


def test_settings_reject_unknown_provider() -> None:
    with pytest.raises(ValueError, match="not supported"):
        Settings.from_env({"AGENT_MODEL_PROVIDER": "unknown"})


def test_settings_load_optional_model_base_url() -> None:
    settings = Settings.from_env({"AGENT_MODEL_BASE_URL": "http://proxy:8080"})

    assert settings.model_base_url == "http://proxy:8080"
    assert Settings.from_env({}).model_base_url is None


def test_anthropic_model_forwards_base_url_to_the_sdk(monkeypatch) -> None:
    captured: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="hello")], model="m"
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        @property
        def messages(self) -> FakeMessages:
            return FakeMessages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    model = AnthropicModel(api_key="key", model_name="m", base_url="http://proxy:8080")
    request = ModelRequest(system_prompt="s", messages=[ModelMessage(role="user", content="c")])
    response = asyncio.run(model.complete(request))

    assert captured["base_url"] == "http://proxy:8080"
    assert captured["api_key"] == "key"
    assert response.text == "hello"


def test_anthropic_model_wraps_sdk_failures_as_runtime_errors(monkeypatch) -> None:
    class FailingMessages:
        async def create(self, **kwargs):
            raise Exception("authentication_error: invalid key")

    class FailingAsyncAnthropic:
        @property
        def messages(self) -> FailingMessages:
            return FailingMessages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = FailingAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    model = AnthropicModel(api_key="bad", model_name="m")
    request = ModelRequest(system_prompt="s", messages=[ModelMessage(role="user", content="c")])

    with pytest.raises(RuntimeError, match="Anthropic provider request failed"):
        asyncio.run(model.complete(request))


def test_deepseek_model_requires_a_key() -> None:
    settings = Settings.from_env(
        {"AGENT_MODEL_PROVIDER": "deepseek", "AGENT_MODEL": "deepseek-chat"}
    )

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekModel.from_settings(settings)


def test_deepseek_model_calls_the_openai_compatible_endpoint(monkeypatch) -> None:
    captured: dict = {}

    class FakeChatCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(message=types.SimpleNamespace(content="hello deepseek"))
                ],
                model="deepseek-chat",
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        @property
        def chat(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(completions=FakeChatCompletions())

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    model = DeepSeekModel(api_key="key", model_name="deepseek-chat", base_url="http://proxy:8080")
    request = ModelRequest(
        system_prompt="s", messages=[ModelMessage(role="user", content="c")]
    )
    response = asyncio.run(model.complete(request))

    assert captured["client"]["api_key"] == "key"
    assert captured["client"]["base_url"] == "http://proxy:8080"
    assert captured["model"] == "deepseek-chat"
    assert response.text == "hello deepseek"


def test_router_default_factories_cover_deepseek() -> None:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"})
    router = ModelRouter(settings)

    model = router.get_model()

    assert isinstance(model, DeepSeekModel)


def test_anthropic_model_forwards_and_parses_native_tools(monkeypatch) -> None:
    captured: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                content=[
                    types.SimpleNamespace(type="text", text="reading now"),
                    types.SimpleNamespace(
                        type="tool_use", id="t1", name="fs_read", input={"path": "a.py"}
                    ),
                ],
                model="m",
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            pass

        @property
        def messages(self) -> FakeMessages:
            return FakeMessages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    model = AnthropicModel(api_key="key", model_name="m")
    request = ModelRequest(
        system_prompt="s",
        messages=[ModelMessage(role="user", content="c")],
        tools=(
            ToolSpec(
                name="fs_read",
                description="Read a file.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        ),
    )
    response = asyncio.run(model.complete(request))

    assert captured["tools"] == [
        {
            "name": "fs_read",
            "description": "Read a file.",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    assert response.text == "reading now"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "t1"
    assert response.tool_calls[0].name == "fs_read"
    assert response.tool_calls[0].arguments == {"path": "a.py"}


def test_anthropic_model_omits_tools_when_none_requested(monkeypatch) -> None:
    captured: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="done")], model="m"
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            pass

        @property
        def messages(self) -> FakeMessages:
            return FakeMessages()

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    model = AnthropicModel(api_key="key", model_name="m")
    request = ModelRequest(system_prompt="s", messages=[ModelMessage(role="user", content="c")])
    response = asyncio.run(model.complete(request))

    assert "tools" not in captured
    assert response.text == "done"
    assert response.tool_calls == ()


def test_deepseek_model_forwards_and_parses_native_tools(monkeypatch) -> None:
    captured: dict = {}

    def tool_call(call_id: str, name: str, arguments: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id=call_id,
            function=types.SimpleNamespace(name=name, arguments=arguments),
        )

    class FakeChatCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content="",
                            tool_calls=[tool_call("c1", "fs_edit", '{"path": "x.py"}')],
                        )
                    )
                ],
                model="deepseek-chat",
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(completions=FakeChatCompletions())

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    model = DeepSeekModel(api_key="key", model_name="deepseek-chat")
    request = ModelRequest(
        system_prompt="s",
        messages=[ModelMessage(role="user", content="c")],
        tools=(
            ToolSpec(name="fs_edit", description="Edit a file.", input_schema={"type": "object"}),
        ),
    )
    response = asyncio.run(model.complete(request))

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fs_edit",
                "description": "Edit a file.",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert response.tool_calls[0].id == "c1"
    assert response.tool_calls[0].name == "fs_edit"
    assert response.tool_calls[0].arguments == {"path": "x.py"}


def test_deepseek_model_tolerates_malformed_tool_arguments(monkeypatch) -> None:
    def tool_call(arguments: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id="c2",
            function=types.SimpleNamespace(name="fs_read", arguments=arguments),
        )

    class FakeChatCompletions:
        async def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content="",
                            tool_calls=[
                                tool_call('{"path": "a.py"'),
                                tool_call("[1, 2, 3]"),
                            ],
                        )
                    )
                ],
                model="deepseek-chat",
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(completions=FakeChatCompletions())

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    model = DeepSeekModel(api_key="key", model_name="deepseek-chat")
    request = ModelRequest(system_prompt="s", messages=[ModelMessage(role="user", content="c")])
    response = asyncio.run(model.complete(request))

    assert response.tool_calls[0].arguments == {}
    assert response.tool_calls[1].arguments == {}


def test_deepseek_model_returns_no_tool_calls_when_absent(monkeypatch) -> None:
    class FakeChatCompletions:
        async def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content="final summary", tool_calls=None)
                    )
                ],
                model="deepseek-chat",
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self) -> types.SimpleNamespace:
            return types.SimpleNamespace(completions=FakeChatCompletions())

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    model = DeepSeekModel(api_key="key", model_name="deepseek-chat")
    request = ModelRequest(system_prompt="s", messages=[ModelMessage(role="user", content="c")])
    response = asyncio.run(model.complete(request))

    assert response.text == "final summary"
    assert response.tool_calls == ()
