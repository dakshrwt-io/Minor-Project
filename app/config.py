"""Environment-backed settings for the coding-agent service."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from os import environ
from pathlib import Path


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"


class McpConfigError(ValueError):
    """Raised when a configured MCP server entry is malformed."""


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One configured MCP server, as supplied through settings.

    A ``url`` entry selects a streamable-HTTP server; otherwise the entry
    launches a stdio server (``command`` + ``args``).
    """

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> McpServerConfig:
        """Build a validated configuration from one JSON object."""

        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise McpConfigError("MCP server entry requires a non-empty string 'name'")

        url = data.get("url")
        if url is not None:
            if not isinstance(url, str) or not url:
                raise McpConfigError("MCP server 'url' must be a non-empty string when present")
            return cls(name=name, url=url)

        command = data.get("command")
        args = data.get("args", [])
        if not isinstance(command, str) or not command:
            raise McpConfigError("MCP server entry requires a non-empty string 'command'")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise McpConfigError("MCP server 'args' must be a list of strings")
        return cls(name=name, command=command, args=list(args))


def parse_mcp_servers(raw: str | None) -> list[McpServerConfig]:
    """Parse the AGENT_MCP_SERVERS JSON list into validated server configurations."""

    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpConfigError("AGENT_MCP_SERVERS must be valid JSON") from exc
    if not isinstance(payload, list):
        raise McpConfigError("AGENT_MCP_SERVERS must be a JSON list of server entries")
    return [McpServerConfig.from_mapping(entry) for entry in payload]


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the gateway and the agent builder."""

    anthropic_api_key: str | None
    deepseek_api_key: str | None
    model_provider: ModelProvider
    model_name: str
    model_base_url: str | None
    max_agent_iterations: int
    mcp_servers: tuple[McpServerConfig, ...]
    session_db_path: str = "data/agent-state.sqlite3"

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
            session_db_path=source.get("AGENT_SESSION_DB", "data/agent-state.sqlite3"),
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
