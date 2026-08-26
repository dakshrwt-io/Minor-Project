import pytest

from app.config import ModelProvider, Settings
from app.models.anthropic import AnthropicModel
from app.models.base import ModelClient, ModelRequest, ModelResponse
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
