import io
from typing import Any

from rich.console import Console

from client.interactive import InteractiveClient, parse_command, render_response


def test_parse_command_splits_slash_commands() -> None:
    assert parse_command("/quit") == ("quit", None)
    assert parse_command("/repo C:\\demo") == ("repo", "C:\\demo")
    assert parse_command("/base-url http://x") == ("base-url", "http://x")


def test_render_response_marks_every_observation_type() -> None:
    response = {
        "session_id": "session-1",
        "plan": {
            "goal": "Update README",
            "steps": [{"id": "inspect", "description": "Read README", "status": "completed"}],
        },
        "status": "completed",
        "summary": "README updated.",
        "observations": [
            {
                "call": {"tool_name": "filesystem", "operation": "read", "path": "README.md"},
                "succeeded": True,
                "output": "Current README",
            },
            {"command": ["python", "-m", "pytest"], "passed": True, "output": "1 passed"},
            {"tool_name": "docs.search_docs", "succeeded": True, "content": ["found one"]},
        ],
    }

    transcript = render_response(response)

    assert "[bold green]completed[/]" in transcript
    assert "[green]\u2713[/] filesystem [cyan]read[/] README.md" in transcript
    assert "[green]\u2713[/] test [cyan]python -m pytest[/]" in transcript
    assert "[green]\u2713[/] external [cyan]docs.search_docs[/]" in transcript
    assert "Session: [dim]session-1[/]" in transcript


def test_render_response_marks_failure_and_escapes_content() -> None:
    response = {
        "session_id": None,
        "plan": {"goal": "Search", "steps": []},
        "status": "failed",
        "summary": "",
        "observations": [
            {
                "tool_name": "docs.missing",
                "succeeded": False,
                "content": [],
                "error": "unknown external tool 'docs.missing' is not advertised",
            }
        ],
    }

    transcript = render_response(response)

    assert "[bold red]failed[/]" in transcript
    assert "[red]\u2717[/] external [cyan]docs.missing[/]" in transcript


def done_event(summary: str) -> dict[str, Any]:
    return {
        "type": "done",
        "status": "completed",
        "summary": summary,
        "response": {
            "session_id": "s-1",
            "plan": {"goal": "G", "steps": []},
            "status": "completed",
            "summary": summary,
        },
    }


def make_repl(
    lines: list[str],
    target_repo,
    monkeypatch,
    fake_stream,
    apply_changes: bool = False,
) -> io.StringIO:
    buffer = io.StringIO()
    client = InteractiveClient(
        base_url="http://x",
        target_repo=target_repo,
        apply_changes=apply_changes,
        timeout=30,
        console=Console(file=buffer),
    )
    responses = iter(lines)

    monkeypatch.setattr("client.interactive.stream_request", fake_stream)
    monkeypatch.setattr(
        "client.interactive.Console.input",
        lambda self, prompt="", password=False, stream=None: next(responses),
    )
    client.run()
    return buffer


def test_repl_streams_live_events_and_finishes_with_a_panel(tmp_path, monkeypatch) -> None:
    events = [
        {
            "type": "plan",
            "plan": {
                "goal": "Fix it",
                "steps": [{"id": "1", "description": "Read", "status": "pending"}],
            },
        },
        {"type": "action", "name": "fs_read", "arguments": {"path": "greeting.py"}},
        {
            "type": "observation",
            "observation": {
                "call": {"tool_name": "filesystem", "operation": "read", "path": "greeting.py"},
                "succeeded": True,
                "output": "def greet(): return \"Hello\"",
            },
        },
        {
            "type": "done",
            "status": "completed",
            "summary": "Fixed.",
            "response": {
                "session_id": "s-1",
                "plan": {"goal": "Fix it", "steps": []},
                "status": "completed",
                "summary": "Fixed.",
            },
        },
    ]

    def fake_stream(base_url, payload, timeout, on_event):
        for event in events:
            on_event(event)
        return events[-1]

    monkeypatch.setattr("client.interactive.stream_request", fake_stream)
    buffer = io.StringIO()
    client = InteractiveClient(
        base_url="http://x",
        target_repo=tmp_path,
        apply_changes=False,
        timeout=30,
        console=Console(file=buffer),
    )
    responses = iter(["Fix it", "/quit"])
    monkeypatch.setattr(
        "client.interactive.Console.input",
        lambda self, prompt="", password=False, stream=None: next(responses),
    )
    client.run()

    output = buffer.getvalue()
    assert "Task: Fix it" in output
    assert "fs_read greeting.py" in output
    assert "filesystem read greeting.py" in output
    assert "Fixed." in output
    # Observations stream live; the final panel must not print them again.
    assert output.count("filesystem read greeting.py") == 1


def test_repl_runs_tasks_and_quits(tmp_path, monkeypatch) -> None:
    payloads = []

    def fake_stream(base_url, payload, timeout, on_event):
        payloads.append(payload)
        on_event(done_event("Fixed."))
        return done_event("Fixed.")

    buffer = make_repl(["Fix the greeting", "/quit"], tmp_path, monkeypatch, fake_stream)

    output = buffer.getvalue()
    assert "Fixed." in output
    assert "Bye." in output
    assert payloads[0]["task"] == "Fix the greeting"


def test_repl_apply_toggle_flows_into_the_payload(tmp_path, monkeypatch) -> None:
    payloads = []

    def fake_stream(base_url, payload, timeout, on_event):
        payloads.append(payload)
        on_event(done_event(""))
        return done_event("")

    buffer = make_repl(
        ["/apply", "Write a file", "/quit"], tmp_path, monkeypatch, fake_stream
    )

    assert payloads[0]["apply_changes"] is True
    assert "change authorization: authorized" in buffer.getvalue()


def test_repl_sends_one_session_id_for_every_message(tmp_path, monkeypatch) -> None:
    payloads = []

    def fake_stream(base_url, payload, timeout, on_event):
        payloads.append(payload)
        on_event(done_event(""))
        return done_event("")

    output = make_repl(["hello", "now help me code", "/quit"], tmp_path, monkeypatch, fake_stream)

    assert len(payloads) == 2
    assert payloads[0]["session_id"] == payloads[1]["session_id"]
    assert payloads[0]["session_id"]
    # The header shows the session the REPL will reuse.
    assert payloads[0]["session_id"] in output.getvalue()


def test_repl_new_command_starts_a_fresh_session(tmp_path, monkeypatch) -> None:
    payloads = []

    def fake_stream(base_url, payload, timeout, on_event):
        payloads.append(payload)
        on_event(done_event(""))
        return done_event("")

    make_repl(["hello", "/new", "hello again", "/quit"], tmp_path, monkeypatch, fake_stream)

    assert len(payloads) == 2
    assert payloads[0]["session_id"] != payloads[1]["session_id"]


def test_repl_rejects_an_invalid_repo_switch(tmp_path, monkeypatch) -> None:
    payloads = []

    def fake_stream(base_url, payload, timeout, on_event):
        payloads.append(payload)
        on_event(done_event(""))
        return done_event("")

    buffer = make_repl(
        [f"/repo {tmp_path / 'missing'}", "Inspect", "/quit"],
        tmp_path,
        monkeypatch,
        fake_stream,
    )

    assert payloads[0]["target_repo"] == str(tmp_path.resolve())
    assert "not an existing directory" in buffer.getvalue()


def test_repl_reports_gateway_errors_without_crashing(tmp_path, monkeypatch) -> None:
    def failing_stream(base_url, payload, timeout, on_event):
        raise RuntimeError("gateway unreachable at http://x/v1/agent/run/stream: refused")

    buffer = make_repl(["Inspect", "/quit"], tmp_path, monkeypatch, failing_stream)

    assert "error:" in buffer.getvalue()
    assert "Bye." in buffer.getvalue()
