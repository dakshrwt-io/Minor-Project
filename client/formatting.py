"""Shared observation formatting for the terminal clients (standard library only).

One source of truth for "what does a filesystem/test/external observation look
like as a line of text". classify_observation extracts the rendering facts
(kind, success flag, subject, output/error/content payload) from one gateway
observation dict, excerpt() truncates detail text, and describe_observation()
renders the plain-text line used by client/terminal.py. client/interactive.py
reuses classify_observation() + excerpt() and layers Rich color/escape on top,
so both clients agree on classification and excerpting and differ only in
surface styling.

Must stay standard-library only: `python -m client` single-shot mode runs
without Rich installed-side dependencies, per the promise in client/terminal.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Observation kinds, one per gateway result type. Kind is detected by payload
# key: filesystem ToolResults carry "call", TestResults carry "command", and
# ExternalToolResults (MCP) carry neither.
FILESYSTEM = "filesystem"
TEST = "test"
EXTERNAL = "external"


@dataclass(frozen=True)
class ObservationFacts:
    """Rendering inputs for one observation, independent of output styling.

    Only the fields relevant to the observation's kind are populated; the
    rest are empty so renderers can branch on `kind` without re-probing keys.
    """

    kind: str  # FILESYSTEM, TEST, or EXTERNAL
    succeeded: bool  # filesystem/external: "succeeded" key; test: "passed" key
    operation: str  # filesystem only: read/write/edit/...
    path: str  # filesystem only: target path relative to the repo root
    command: str  # test only: the joined command line
    tool_name: str  # external only: server-qualified MCP tool name
    output: str  # stdout/result payload on success
    error: str  # error message on failure
    content: str  # external only: joined content items


def classify_observation(observation: dict[str, Any]) -> ObservationFacts:
    """Extract rendering facts from one observation dict.

    Success is read from "succeeded" — except for tests, which use "passed"
    (a test that exits non-zero did not "fail to run", it failed as a test).
    """

    if "call" in observation:
        call = observation["call"]
        return ObservationFacts(
            kind=FILESYSTEM,
            succeeded=bool(observation.get("succeeded")),
            operation=str(call.get("operation", "")),
            path=str(call.get("path", "")),
            command="",
            tool_name="",
            output=observation.get("output", ""),
            error=observation.get("error", ""),
            content="",
        )
    if "command" in observation:
        return ObservationFacts(
            kind=TEST,
            succeeded=bool(observation.get("passed")),
            operation="",
            path="",
            command=" ".join(observation.get("command", ())),
            tool_name="",
            output=observation.get("output", ""),
            error=observation.get("error", ""),
            content="",
        )
    return ObservationFacts(
        kind=EXTERNAL,
        succeeded=bool(observation.get("succeeded")),
        operation="",
        path="",
        command="",
        tool_name=str(observation.get("tool_name", "")),
        output=observation.get("output", ""),
        error=observation.get("error", ""),
        content=" ".join(observation.get("content", ())),
    )


def excerpt(value: Any, limit: int = 240) -> str:
    """Collapse all whitespace runs to single spaces, then truncate to `limit`."""

    normalized = " ".join(str(value).split())
    if len(normalized) > limit:
        normalized = normalized[: limit - 1].rstrip() + "…"
    return normalized


def describe_action(event: dict[str, Any]) -> str:
    """Render one live gateway 'action' event as a short plain-text line.

    Event shape: {"type": "action", "name": <tool name>, "arguments": {...}}.
    Filesystem tools lead with their path; anything else shows the compact JSON.
    """

    name = str(event.get("name", ""))
    arguments = event.get("arguments")
    if not isinstance(arguments, dict):
        return name
    path = arguments.get("path")
    if isinstance(path, str) and path:
        return f"{name} {path}"
    compact = json.dumps(arguments, sort_keys=True, separators=(",", ":")) if arguments else ""
    return f"{name}({compact})" if compact else name


def describe_observation(observation: dict[str, Any]) -> str:
    """Render one observation as the plain-text line used by the terminal client.

    Success shows the output payload (or "no output"); failure shows the error
    message once — never repeated as both outcome and detail.
    """

    facts = classify_observation(observation)
    if facts.kind == FILESYSTEM:
        if facts.succeeded:
            detail = facts.output or "no output"
            return f"filesystem {facts.operation} {facts.path}: succeeded; {excerpt(detail)}"
        return f"filesystem {facts.operation} {facts.path}: failed: {excerpt(facts.error or 'no output')}"
    if facts.kind == TEST:
        outcome = "passed" if facts.succeeded else "failed"
        detail = facts.output or facts.error
        suffix = f"; {excerpt(detail)}" if detail else ""
        return f"test command: {outcome}{suffix}"
    if facts.succeeded:
        detail = facts.content or "no output"
        return f"external {facts.tool_name}: succeeded; {excerpt(detail)}"
    return f"external {facts.tool_name}: failed: {excerpt(facts.error or 'no output')}"
