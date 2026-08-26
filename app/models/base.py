"""Provider-neutral language-model contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelMessage(BaseModel):
    """A conversational message supplied to a language model."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ModelRequest(BaseModel):
    """All provider-independent inputs needed for a text completion."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str = Field(min_length=1)
    messages: list[ModelMessage] = Field(min_length=1)
    max_tokens: int = Field(default=1024, ge=1, le=8192)


class ModelResponse(BaseModel):
    """Normalized text returned from a model provider."""

    model_config = ConfigDict(frozen=True)

    text: str
    model_name: str


class ModelClient(ABC):
    """Minimal boundary that keeps application code provider independent."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized completion for one model request."""
