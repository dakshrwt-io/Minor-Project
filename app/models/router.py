"""Settings-driven selection of language-model providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.config import ModelProvider, Settings
from app.models.anthropic import AnthropicModel
from app.models.base import ModelClient
from app.models.deepseek import DeepSeekModel

ModelFactory = Callable[[Settings], ModelClient]


class ModelRouter:
    """Create the configured provider without leaking it into application code."""

    def __init__(self, settings: Settings, factories: Mapping[ModelProvider, ModelFactory] | None = None):
        self._settings = settings
        self._factories = dict(
            factories
            or {
                ModelProvider.ANTHROPIC: AnthropicModel.from_settings,
                ModelProvider.DEEPSEEK: DeepSeekModel.from_settings,
            }
        )

    def get_model(self) -> ModelClient:
        try:
            factory = self._factories[self._settings.model_provider]
        except KeyError as exc:
            raise ValueError(
                f"No model factory registered for {self._settings.model_provider.value}"
            ) from exc
        return factory(self._settings)
