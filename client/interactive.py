"""Interactive terminal UI for the coding-agent gateway.

A REPL in the spirit of Claude Code / opencode: repeated task entry, rendered
transcripts, and slash commands. Rendering uses Rich markup so transcripts are
readable on both true terminals and redirected output.

Observation classification and excerpting come from client/formatting.py (the
same source of truth the plain-text client uses); this module only adds color,
Rich markup escaping, and the REPL loop on top.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from client.formatting import FILESYSTEM, TEST, classify_observation, describe_action, excerpt
from client.terminal import StreamUnavailable, build_payload, post_request, stream_request

_STATUS_BADGES = {
    "completed": "[green]\u2713[/]",
    "failed": "[red]\u2717[/]",
    "pending": "[dim]\u00b7[/]",
}


def parse_command(line: str) -> tuple[str, str | None]:
    """Split an interactive slash command into (command, argument)."""

    parts = line.strip().split(maxsplit=1)
    command = parts[0].lstrip("/").lower()
    argument = parts[1].strip() if len(parts) > 1 else None
    return command, argument


def render_response(response: dict[str, Any], include_observations: bool = True) -> str:
    """Build the rich-markup transcript for one agent response.

    With streaming, observations are printed live as they happen, so the final
    panel passes include_observations=False to avoid printing them twice.
    """

    lines: list[str] = []
    plan = response.get("plan", {})
    lines.append(f"[bold]Task:[/] {escape(plan.get('goal', ''))}")
    for index, step in enumerate(plan.get("steps", []), start=1):
        badge = _STATUS_BADGES.get(step.get("status", "pending"), "[dim]\u00b7[/]")
        lines.append(f"  {badge} {index}. {escape(step.get('description', ''))}")

    status = response.get("status", "unknown")
    status_text = (
        "[bold green]completed[/]" if status == "completed" else f"[bold red]{escape(status)}[/]"
    )
    lines.append(f"Status: {status_text}")
    session_id = response.get("session_id")
    if session_id:
        lines.append(f"Session: [dim]{escape(session_id)}[/]")
    summary = response.get("summary", "")
    if summary:
        lines.append(f"Summary: {escape(summary)}")

    if include_observations:
        observations = response.get("observations", [])
        if observations:
            lines.append("")
            lines.append("[bold]Observations[/]")
            for observation in observations:
                lines.append(f"  {_describe_observation(observation)}")
    return "\n".join(lines)


def _describe_observation(observation: dict[str, Any]) -> str:
    """Render one observation as a Rich-markup line (same facts as the plain client).

    Detail selection differs from the plain-text client by design parity with
    the original implementation: on success the output payload is shown, on
    failure the error message is shown.
    """

    facts = classify_observation(observation)
    if facts.kind == FILESYSTEM:
        if facts.succeeded:
            return (
                f"[green]\u2713[/] filesystem [cyan]{escape(facts.operation)}[/] {escape(facts.path)} "
                f"[dim]\u2014 {_excerpt(facts.output)}[/]"
            )
        return (
            f"[red]\u2717[/] filesystem [cyan]{escape(facts.operation)}[/] {escape(facts.path)} "
            f"[red]\u2014 {_excerpt(facts.error)}[/]"
        )
    if facts.kind == TEST:
        if facts.succeeded:
            return (
                f"[green]\u2713[/] test [cyan]{escape(facts.command)}[/] "
                f"[dim]\u2014 {_excerpt(facts.output)}[/]"
            )
        return (
            f"[red]\u2717[/] test [cyan]{escape(facts.command)}[/] "
            f"[red]\u2014 {_excerpt(facts.error)}[/]"
        )
    if facts.succeeded:
        return (
            f"[green]\u2713[/] external [cyan]{escape(facts.tool_name)}[/] "
            f"[dim]\u2014 {_excerpt(facts.content)}[/]"
        )
    return (
        f"[red]\u2717[/] external [cyan]{escape(facts.tool_name)}[/] "
        f"[red]\u2014 {_excerpt(facts.error)}[/]"
    )


def _excerpt(value: Any, limit: int = 240) -> str:
    """Excerpt via the shared formatter, then escape for Rich markup."""

    return escape(excerpt(value, limit))


class InteractiveClient:
    """Repeated task entry with rendered transcripts and slash commands."""

    def __init__(
        self,
        *,
        base_url: str,
        target_repo: Path,
        apply_changes: bool,
        timeout: float,
        console: Console | None = None,
    ) -> None:
        self._base_url = base_url
        self._target_repo = target_repo
        self._apply_changes = apply_changes
        self._timeout = timeout
        self._console = console or Console()
        # One session for the whole REPL run: every message carries this id, so
        # the gateway's conversation memory links the turns together. Closing
        # and restarting the client starts a new session by construction.
        self._session_id = str(uuid4())

    def run(self) -> int:
        """Run the REPL until the user quits."""

        self._print_header()
        self._console.print("[dim]Type a task, or /help for commands.[/]")
        while True:
            try:
                line = self._console.input("[bold cyan]\u276f [/]").strip()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if self._run_command(line):
                    break
                continue
            self._submit(line)
        self._console.print("[dim]Bye.[/]")
        return 0

    def _run_command(self, line: str) -> bool:
        command, argument = parse_command(line)
        if command in {"quit", "exit"}:
            return True
        if command == "help":
            self._print_help()
        elif command == "apply":
            self._apply_changes = not self._apply_changes
            state = "authorized" if self._apply_changes else "read-only"
            self._console.print(f"change authorization: [bold]{state}[/]")
        elif command == "repo":
            if not argument:
                self._console.print(f"current target: {self._target_repo.resolve()}")
            else:
                candidate = Path(argument)
                if not candidate.is_dir():
                    self._console.print(f"[red]error:[/] not an existing directory: {argument}")
                else:
                    self._target_repo = candidate
                    self._console.print(f"target: {candidate.resolve()}")
        elif command == "base-url":
            if not argument:
                self._console.print(f"current gateway: {self._base_url}")
            else:
                self._base_url = argument
                self._console.print(f"gateway: {argument}")
        elif command == "clear":
            self._console.clear()
        elif command == "new":
            self._session_id = str(uuid4())
            self._console.print(f"new session: [dim]{escape(self._session_id)}[/]")
        else:
            self._console.print(f"[red]unknown command:[/] /{command} ([dim]/help[/])")
        return False

    def _submit(self, task: str) -> None:
        payload = build_payload(
            task, self._target_repo, self._apply_changes, session_id=self._session_id
        )
        try:
            try:
                event = stream_request(
                    self._base_url, payload, self._timeout, self._render_live_event
                )
            except StreamUnavailable:
                # Older gateway without the streaming endpoint: block, spin, render once.
                with self._console.status("[cyan]Agent is working\u2026[/]"):
                    response = post_request(self._base_url, payload, self._timeout)
                self._console.print(
                    Panel(
                        render_response(response),
                        title="[bold]Agent result[/]",
                        border_style="cyan",
                    )
                )
                return
        except RuntimeError as exc:
            self._console.print(f"[bold red]error:[/] {escape(str(exc))}")
            return
        if event.get("type") == "error":
            return  # already rendered live by _render_live_event
        self._console.print(
            Panel(
                render_response(event.get("response") or {}, include_observations=False),
                title="[bold]Agent result[/]",
                border_style="cyan",
            )
        )

    def _render_live_event(self, event: dict[str, Any]) -> None:
        """Print one streaming event line as it arrives from the gateway."""

        kind = event.get("type")
        if kind == "plan":
            plan = event.get("plan", {})
            self._console.print(f"[bold]Task:[/] {escape(plan.get('goal', ''))}")
            for index, step in enumerate(plan.get("steps", []), start=1):
                self._console.print(
                    f"  [dim]\u00b7[/] {index}. {escape(step.get('description', ''))}"
                )
        elif kind == "action":
            self._console.print(f"  [cyan]\u2192[/] [bold]{escape(describe_action(event))}[/]")
        elif kind == "observation":
            observation = event.get("observation")
            if isinstance(observation, dict):
                self._console.print(f"  {_describe_observation(observation)}")
        elif kind == "error":
            raise RuntimeError(
                f"gateway error {event.get('status_code', '')}: {event.get('detail', '')}"
            )

    def _print_header(self) -> None:
        self._console.rule("[bold]Autonomous Coding Agent[/]")
        self._console.print(f"  gateway: {self._base_url}")
        self._console.print(f"  target:  {self._target_repo.resolve()}")
        self._console.print(
            f"  changes: {'[green]authorized[/]' if self._apply_changes else '[yellow]read-only[/]'}"
        )
        self._console.print(f"  session: [dim]{escape(self._session_id)}[/]")
        self._console.rule()

    def _print_help(self) -> None:
        state = "authorized" if self._apply_changes else "read-only"
        self._console.print(
            "[bold]Commands[/]\n"
            f"  /apply          toggle change authorization (currently {state})\n"
            "  /repo <path>    switch target repository (or show current)\n"
            "  /base-url <url> switch gateway URL (or show current)\n"
            "  /clear          clear the screen\n"
            "  /new            start a fresh session (forget earlier turns)\n"
            "  /help           show this help\n"
            "  /quit, /exit    leave the client\n"
            "[dim]Anything else is sent to the agent as a task.[/]"
        )
