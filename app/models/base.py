"""Provider-neutral language-model contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelMessage(BaseModel):
    """A conversational message supplied to a language model."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ToolSpec(BaseModel):
    """Provider-neutral schema for one callable tool."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ModelToolCall(BaseModel):
    """A tool invocation requested by the model."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    """All provider-independent inputs needed for a text completion.

    `max_tokens` bounds the model's whole reply. Reasoning-style providers
    (e.g. DeepSeek's hybrid-thinking models) spend the same budget on hidden
    `reasoning_content` before emitting any visible content or tool calls —
    with a small budget they return `finish_reason=length` with an empty
    body, which looks like a silent model failure. The default therefore
    leaves room for reasoning plus a full tool-call payload (a single
    fs_create can carry an entire file's content).
    """

    model_config = ConfigDict(frozen=True)

    system_prompt: str = Field(min_length=1)
    messages: list[ModelMessage] = Field(min_length=1)
    max_tokens: int = Field(default=8192, ge=1, le=8192)
    tools: tuple[ToolSpec, ...] = ()


class ModelResponse(BaseModel):
    """Normalized text and tool calls returned from a model provider."""

    model_config = ConfigDict(frozen=True)

    text: str
    model_name: str
    tool_calls: tuple[ModelToolCall, ...] = ()


class ModelClient(ABC):
    """Minimal boundary that keeps application code provider independent."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized completion for one model request."""
