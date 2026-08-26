"""Anthropic implementation of the provider-neutral model contract."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.models.base import ModelClient, ModelRequest, ModelResponse


class AnthropicModel(ModelClient):
    """Adapter that imports the Anthropic SDK only for a live completion."""

    def __init__(self, api_key: str, model_name: str) -> None:
        self._api_key = api_key
        self._model_name = model_name

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicModel:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the Anthropic provider")
        return cls(api_key=settings.anthropic_api_key, model_name=settings.model_name)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError(
                "The Anthropic SDK is not installed. Install the project runtime dependencies."
            ) from exc

        client = AsyncAnthropic(api_key=self._api_key)
        response: Any = await client.messages.create(
            model=self._model_name,
            max_tokens=request.max_tokens,
            system=request.system_prompt,
            messages=[message.model_dump() for message in request.messages],
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
        return ModelResponse(text=text, model_name=response.model)
