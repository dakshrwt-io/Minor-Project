from pathlib import Path

from client.terminal import build_payload, main, render_event


def test_build_payload_resolves_the_target_repo(tmp_path: Path) -> None:
    payload = build_payload("Review README", tmp_path, False)

    assert payload == {
        "task": "Review README",
        "target_repo": str(tmp_path.resolve()),
        "apply_changes": False,
    }


def test_build_payload_includes_the_session_id_when_supplied(tmp_path: Path) -> None:
    payload = build_payload("Review README", tmp_path, False, session_id="repl-session-7")

    assert payload["session_id"] == "repl-session-7"
    # Single-shot requests omit it entirely: the gateway mints a fresh session.
    assert "session_id" not in build_payload("Review README", tmp_path, False)


def test_main_rejects_a_missing_target_repo(tmp_path: Path, capsys) -> None:
    code = main(["--task", "Do it", "--target-repo", str(tmp_path / "missing")])

    assert code == 2
    assert "target repo must be an existing directory" in capsys.readouterr().err


def test_main_streams_live_events_and_exit_codes(tmp_path: Path, monkeypatch, capsys) -> None:
    events = [
        {"type": "plan", "plan": {"goal": "Fix it", "steps": []}},
        {
            "type": "observation",
            "observation": {
                "call": {"tool_name": "filesystem", "operation": "read", "path": "a.py"},
                "succeeded": True,
                "output": "content",
            },
        },
        {"type": "done", "status": "completed", "summary": "ok", "response": {"session_id": "s1"}},
    ]
    snapshot: dict = {}

    def fake_stream(base_url, payload, timeout, on_event):
        snapshot["base_url"] = base_url
        snapshot["payload"] = payload
        for event in events:
            on_event(event)
        return events[-1]

    monkeypatch.setattr("client.terminal.stream_request", fake_stream)

    code = main(["--task", "Fix it", "--target-repo", str(tmp_path), "--base-url", "http://x"])

    assert code == 0
    assert snapshot["base_url"] == "http://x"
    assert snapshot["payload"]["task"] == "Fix it"
    output = capsys.readouterr().out
    assert "Task: Fix it" in output
    assert "filesystem read a.py: succeeded; content" in output
    assert "Status: completed" in output


def test_main_maps_unicode_observation_output(tmp_path: Path, monkeypatch, capsys) -> None:
    events = [
        {
            "type": "observation",
            "observation": {
                "call": {"tool_name": "filesystem", "operation": "read", "path": "f.py"},
                "succeeded": True,
                "output": "\ufeffcaf\u00e9",
            },
        },
        {"type": "done", "status": "completed", "summary": "", "response": {}},
    ]

    def fake_stream(base_url, payload, timeout, on_event):
        for event in events:
            on_event(event)
        return events[-1]

    monkeypatch.setattr("client.terminal.stream_request", fake_stream)

    code = main(["--task", "Inspect", "--target-repo", str(tmp_path)])

    assert code == 0
    assert "caf\u00e9" in capsys.readouterr().out


def test_main_returns_failed_code_for_failed_status(tmp_path: Path, monkeypatch) -> None:
    event = {"type": "done", "status": "failed", "summary": "", "response": {}}

    def fake_stream(base_url, payload, timeout, on_event):
        on_event(event)
        return event

    monkeypatch.setattr("client.terminal.stream_request", fake_stream)

    code = main(["--task", "Do it", "--target-repo", str(tmp_path)])

    assert code == 1


def test_main_reports_gateway_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    def failing_stream(base_url, payload, timeout, on_event):
        raise RuntimeError("gateway unreachable at http://x/v1/agent/run/stream: refused")

    monkeypatch.setattr("client.terminal.stream_request", failing_stream)

    code = main(["--task", "Do it", "--target-repo", str(tmp_path)])

    assert code == 2
    assert "gateway unreachable" in capsys.readouterr().err


def test_main_maps_a_stream_error_event_to_exit_code_2(tmp_path: Path, monkeypatch, capsys) -> None:
    events = [{"type": "error", "status_code": 400, "detail": "bad request"}]

    def fake_stream(base_url, payload, timeout, on_event):
        for event in events:
            on_event(event)
        return events[-1]

    monkeypatch.setattr("client.terminal.stream_request", fake_stream)

    code = main(["--task", "Do it", "--target-repo", str(tmp_path), "--base-url", "http://x"])

    assert code == 2
    assert "error: bad request" in capsys.readouterr().out


def test_render_event_renders_streaming_progress_lines() -> None:
    plan_event = {
        "type": "plan",
        "plan": {
            "goal": "Fix it",
            "steps": [{"id": "1", "description": "Read", "status": "pending"}],
        },
    }
    assert render_event(plan_event) == ["Task: Fix it", "  1. [pending] Read"]
    assert render_event({"type": "action", "name": "fs_read", "arguments": {"path": "a.py"}}) == [
        "→ fs_read a.py"
    ]
    observation_lines = render_event(
        {
            "type": "observation",
            "observation": {
                "call": {"tool_name": "filesystem", "operation": "read", "path": "a.py"},
                "succeeded": True,
                "output": "content",
            },
        }
    )
    assert observation_lines == ["  - filesystem read a.py: succeeded; content"]
    done_lines = render_event(
        {
            "type": "done",
            "status": "completed",
            "summary": "Done.",
            "response": {"session_id": "s1"},
        }
    )
    assert done_lines == ["Status: completed", "Session: s1", "Summary: Done."]
