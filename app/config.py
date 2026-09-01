"""Environment-backed settings for the coding-agent service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from os import environ
from pathlib import Path

from app.mcp.connection import McpServerConfig, parse_mcp_servers


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by gateway and model-routing components."""

    anthropic_api_key: str | None
    deepseek_api_key: str | None
    model_provider: ModelProvider
    model_name: str
    model_base_url: str | None
    max_agent_iterations: int
    mcp_servers: tuple[McpServerConfig, ...]

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> Settings:
        """Build settings from an environment-like mapping.

        Accepting an explicit mapping keeps configuration tests independent from
        the machine running them. With no mapping, a repository-root ``.env``
        file is loaded first; real environment variables always win over it.
        """

        source = environ if values is None else values
        if values is None:
            _load_dot_env()
        iterations = int(source.get("AGENT_MAX_ITERATIONS", "6"))
        if iterations < 1:
            raise ValueError("AGENT_MAX_ITERATIONS must be at least 1")

        try:
            provider = ModelProvider(source.get("AGENT_MODEL_PROVIDER", "anthropic"))
        except ValueError as exc:
            raise ValueError("AGENT_MODEL_PROVIDER is not supported") from exc

        return cls(
            anthropic_api_key=source.get("ANTHROPIC_API_KEY") or None,
            deepseek_api_key=source.get("DEEPSEEK_API_KEY") or None,
            model_provider=provider,
            model_name=source.get("AGENT_MODEL", "claude-sonnet-4-20250514"),
            model_base_url=source.get("AGENT_MODEL_BASE_URL") or None,
            max_agent_iterations=iterations,
            mcp_servers=tuple(parse_mcp_servers(source.get("AGENT_MCP_SERVERS"))),
        )


def _load_dot_env() -> None:
    """Load ``KEY=VALUE`` lines from a repository-root ``.env`` file.

    Existing environment variables are never overridden, so real shell
    configuration takes precedence. Comments and quoted values are handled;
    more exotic dotenv syntax is intentionally out of scope.
    """

    dot_env_path = Path.cwd() / ".env"
    if not dot_env_path.is_file():
        return
    for line in dot_env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or key in environ:
            continue
        value = value.strip().strip("\"'").strip()
        if value:
            environ[key] = value
