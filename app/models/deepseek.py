"""DeepSeek implementation of the provider-neutral model contract.

DeepSeek serves an OpenAI-compatible API; this adapter imports the OpenAI
SDK only for a live completion. ``AGENT_MODEL_BASE_URL`` overrides the default
DeepSeek endpoint when set.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall

_DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekModel(ModelClient):
    """Adapter over DeepSeek's OpenAI-compatible chat completions endpoint."""

    def __init__(self, api_key: str, model_name: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._base_url = base_url or _DEFAULT_BASE_URL

    @classmethod
    def from_settings(cls, settings: Settings) -> DeepSeekModel:
        if not settings.deepseek_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required for the DeepSeek provider; "
                "set it in the gateway terminal, add it to .env in the repository "
                "root, then restart uvicorn"
            )
        return cls(
            api_key=settings.deepseek_api_key,
            model_name=settings.model_name,
            base_url=settings.model_base_url,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The OpenAI SDK is not installed. Install the project runtime dependencies."
            ) from exc

        try:
            client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
            payload: dict[str, Any] = {
                "model": self._model_name,
                "max_tokens": request.max_tokens,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    *[message.model_dump() for message in request.messages],
                ],
            }
            if request.tools:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        },
                    }
                    for tool in request.tools
                ]
            response: Any = await client.chat.completions.create(**payload)
        except Exception as exc:
            raise RuntimeError(f"DeepSeek provider request failed: {exc}") from exc
        message = response.choices[0].message
        text = message.content or ""
        tool_calls = tuple(
            self._parse_tool_call(call) for call in (getattr(message, "tool_calls", None) or ())
        )
        return ModelResponse(text=text, model_name=response.model, tool_calls=tool_calls)

    @staticmethod
    def _parse_tool_call(call: Any) -> ModelToolCall:
        """Normalize one OpenAI-style tool call.

        Unparseable argument payloads become empty arguments so downstream
        tool validation rejects them as an auditable failed observation
        instead of crashing the request.
        """

        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return ModelToolCall(id=call.id or "", name=call.function.name, arguments=arguments)
