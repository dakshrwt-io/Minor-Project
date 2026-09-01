"""Anthropic implementation of the provider-neutral model contract."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall


class AnthropicModel(ModelClient):
    """Adapter that imports the Anthropic SDK only for a live completion."""

    def __init__(self, api_key: str, model_name: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._base_url = base_url

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicModel:
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required for the Anthropic provider; "
                "set it in the gateway terminal, add it to .env in the repository "
                "root, then restart uvicorn"
            )
        return cls(
            api_key=settings.anthropic_api_key,
            model_name=settings.model_name,
            base_url=settings.model_base_url,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError(
                "The Anthropic SDK is not installed. Install the project runtime dependencies."
            ) from exc

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        payload: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": request.max_tokens,
            "system": request.system_prompt,
            "messages": [message.model_dump() for message in request.messages],
        }
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        try:
            client = AsyncAnthropic(**client_kwargs)
            response: Any = await client.messages.create(**payload)
        except Exception as exc:
            raise RuntimeError(f"Anthropic provider request failed: {exc}") from exc
        text = "\n".join(block.text for block in response.content if block.type == "text")
        tool_calls = tuple(
            ModelToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        )
        return ModelResponse(text=text, model_name=response.model, tool_calls=tool_calls)
