"""Environment-backed settings for the coding-agent service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import environ
from pathlib import Path
from typing import Mapping


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by gateway and model-routing components."""

    anthropic_api_key: str | None
    model_provider: ModelProvider
    model_name: str
    max_agent_iterations: int
    session_database_path: Path

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> Settings:
        """Build settings from an environment-like mapping.

        Accepting an explicit mapping keeps configuration tests independent from
        the machine running them.
        """

        source = environ if values is None else values
        iterations = int(source.get("AGENT_MAX_ITERATIONS", "6"))
        if iterations < 1:
            raise ValueError("AGENT_MAX_ITERATIONS must be at least 1")

        try:
            provider = ModelProvider(source.get("AGENT_MODEL_PROVIDER", "anthropic"))
        except ValueError as exc:
            raise ValueError("AGENT_MODEL_PROVIDER is not supported") from exc

        return cls(
            anthropic_api_key=source.get("ANTHROPIC_API_KEY") or None,
            model_provider=provider,
            model_name=source.get("AGENT_MODEL", "claude-sonnet-4-20250514"),
            max_agent_iterations=iterations,
            session_database_path=Path(
                source.get("AGENT_SESSION_DATABASE", "data/agent-state.sqlite3")
            ),
        )
