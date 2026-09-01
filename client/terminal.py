"""Single-shot terminal client for the gateway's agent endpoint.

Uses only the standard library so the demo client needs no extra dependencies.
Run from the repository root: ``python -m client --task ... --target-repo ...``
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from client.formatting import describe_action, describe_observation

_SSE_PREFIX = "data: "
_TERMINAL_EVENTS = {"done", "error"}


class StreamUnavailable(RuntimeError):
    """The gateway has no streaming endpoint; the caller should fall back."""


def build_payload(
    task: str,
    target_repo: Path,
    apply_changes: bool,
    session_id: str | None = None,
) -> dict[str, object]:
    """Build the gateway request body for one agent invocation.

    `session_id` is optional: the interactive REPL supplies its one id for
    every message so its conversation shares a session; single-shot requests
    omit it and the gateway mints a fresh session per invocation.
    """

    payload: dict[str, object] = {
        "task": task,
        "target_repo": str(target_repo.resolve()),
        "apply_changes": apply_changes,
    }
    if session_id:
        payload["session_id"] = session_id
    return payload


def post_request(base_url: str, payload: dict[str, object], timeout: float) -> dict[str, Any]:
    """POST the payload to the agent endpoint and return the JSON response."""

    endpoint = f"{base_url.rstrip('/')}/v1/agent/run"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = f": {body.get('detail', '')}"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise RuntimeError(f"gateway returned HTTP {exc.code}{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"gateway unreachable at {endpoint}: {exc}") from exc


def stream_request(
    base_url: str,
    payload: dict[str, object],
    timeout: float,
    on_event: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """POST the payload to the streaming endpoint and deliver events live.

    Parses server-sent events (`data: <json>` lines) as they arrive and calls
    `on_event` for each, so callers can render progress in real time. Returns
    the terminal event (`done` or `error`). Raises StreamUnavailable when the
    gateway predates the streaming endpoint (HTTP 404).
    """

    endpoint = f"{base_url.rstrip('/')}/v1/agent/run/stream"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith(_SSE_PREFIX):
                    continue
                event = json.loads(line[len(_SSE_PREFIX) :])
                on_event(event)
                if event.get("type") in _TERMINAL_EVENTS:
                    return event
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise StreamUnavailable(f"no streaming endpoint at {endpoint}") from exc
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = f": {body.get('detail', '')}"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise RuntimeError(f"gateway returned HTTP {exc.code}{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"gateway unreachable at {endpoint}: {exc}") from exc
    raise RuntimeError("gateway closed the stream before the run finished")


def render(response: dict[str, Any]) -> str:
    """Render one agent response as a readable terminal transcript."""

    lines: list[str] = []
    plan = response.get("plan", {})
    lines.append(f"Task: {plan.get('goal', '')}")
    for index, step in enumerate(plan.get("steps", []), start=1):
        lines.append(f"  {index}. [{step.get('status', 'pending')}] {step.get('description', '')}")
    status = response.get("status", "unknown")
    lines.append(f"Status: {status}")
    session_id = response.get("session_id")
    if session_id:
        lines.append(f"Session: {session_id}")
    summary = response.get("summary", "")
    if summary:
        lines.append(f"Summary: {summary}")
    observations = response.get("observations", [])
    if observations:
        lines.append("Observations:")
        for observation in observations:
            lines.append(f"  - {describe_observation(observation)}")
    return "\n".join(lines)


def render_event(event: dict[str, Any]) -> list[str]:
    """Render one live stream event as terminal lines for real-time display."""

    kind = event.get("type")
    if kind == "plan":
        plan = event.get("plan", {})
        lines = [f"Task: {plan.get('goal', '')}"]
        for index, step in enumerate(plan.get("steps", []), start=1):
            status = step.get("status", "pending")
            lines.append(f"  {index}. [{status}] {step.get('description', '')}")
        return lines
    if kind == "action":
        return [f"→ {describe_action(event)}"]
    if kind == "observation":
        observation = event.get("observation")
        if isinstance(observation, dict):
            return [f"  - {describe_observation(observation)}"]
        return []
    if kind == "done":
        lines = [f"Status: {event.get('status', 'unknown')}"]
        response = event.get("response") or {}
        session_id = response.get("session_id")
        if session_id:
            lines.append(f"Session: {session_id}")
        summary = event.get("summary", "")
        if summary:
            lines.append(f"Summary: {summary}")
        return lines
    if kind == "error":
        return [f"error: {event.get('detail', 'unknown gateway error')}"]
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m client",
        description="Run one coding-agent task through the gateway.",
    )
    parser.add_argument(
        "--task",
        help="natural-language task for the agent (optional in interactive mode)",
    )
    parser.add_argument(
        "--target-repo",
        required=True,
        type=Path,
        help="existing target repository the agent may operate on",
    )
    parser.add_argument(
        "--apply-changes",
        action="store_true",
        help="authorize create, write, and edit operations",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="open the interactive REPL client instead of running one task",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="gateway base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="wait for the finished response instead of streaming live progress",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="request timeout in seconds (default: 120)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Target repositories may contain any Unicode; Windows consoles default to
    # cp1252, so force UTF-8 with replacement instead of crashing on encode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if not args.target_repo.is_dir():
        print(f"error: target repo must be an existing directory: {args.target_repo}", file=sys.stderr)
        return 2
    if args.interactive:
        from client.interactive import InteractiveClient

        return InteractiveClient(
            base_url=args.base_url,
            target_repo=args.target_repo,
            apply_changes=args.apply_changes,
            timeout=args.timeout,
        ).run()
    if not args.task:
        build_parser().error("the following argument is required: --task (unless --interactive)")
    payload = build_payload(args.task, args.target_repo, args.apply_changes)
    if not args.no_stream:
        try:
            event = stream_request(
                args.base_url,
                payload,
                args.timeout,
                lambda received: print("\n".join(render_event(received))),
            )
        except StreamUnavailable:
            pass  # older gateway: fall through to the plain POST below
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        else:
            if event.get("type") == "error":
                return 2
            return 0 if event.get("status") == "completed" else 1
    try:
        response = post_request(args.base_url, payload, args.timeout)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render(response))
    return 0 if response.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
