from pathlib import Path

import pytest

from client.terminal import StreamUnavailable, build_payload, main, render, render_event


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


def test_render_shows_plan_status_and_every_observation_type() -> None:
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
                "call": {
                    "tool_name": "filesystem",
                    "operation": "read",
                    "path": "README.md",
                },
                "succeeded": True,
                "output": "Current README",
            },
            {"command": ["python", "-m", "pytest"], "passed": True, "output": "1 passed"},
            {
                "tool_name": "docs.search_docs",
                "succeeded": True,
                "content": ["found one"],
            },
        ],
    }

    output = render(response)

    assert "Task: Update README" in output
    assert "[completed] Read README" in output
    assert "Status: completed" in output
    assert "Session: session-1" in output
    assert "Summary: README updated." in output
    assert "filesystem read README.md: succeeded; Current README" in output
    assert "test command: passed; 1 passed" in output
    assert "external docs.search_docs: succeeded; found one" in output


def test_render_marks_failed_observations(tmp_path: Path) -> None:
    response = {
        "session_id": None,
        "plan": {"goal": "Search", "steps": [{"id": "act", "description": "Search", "status": "pending"}]},
        "status": "completed",
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

    output = render(response)

    assert "external docs.missing: failed: unknown external tool" in output


def test_main_uses_gateway_response_and_exit_code(tmp_path: Path, monkeypatch, capsys) -> None:
    payload_snapshot: dict = {}

    def fake_post(base_url, payload, timeout):
        payload_snapshot["base_url"] = base_url
        payload_snapshot["payload"] = payload
        return {"plan": {"goal": "G", "steps": []}, "status": "completed", "summary": "ok"}

    monkeypatch.setattr("client.terminal.post_request", fake_post)

    code = main(
        ["--task", "Do it", "--target-repo", str(tmp_path), "--base-url", "http://x", "--no-stream"]
    )

    assert code == 0
    assert payload_snapshot["payload"]["task"] == "Do it"
    assert payload_snapshot["base_url"] == "http://x"
    assert "Status: completed" in capsys.readouterr().out


def test_main_returns_failed_code_for_failed_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "client.terminal.post_request",
        lambda *_: {"plan": {"goal": "G", "steps": []}, "status": "failed", "summary": ""},
    )

    code = main(["--task", "Do it", "--target-repo", str(tmp_path), "--no-stream"])

    assert code == 1


def test_main_rejects_a_missing_target_repo(tmp_path: Path, capsys) -> None:
    code = main(["--task", "Do it", "--target-repo", str(tmp_path / "missing")])

    assert code == 2
    assert "target repo must be an existing directory" in capsys.readouterr().err


def test_main_reports_gateway_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_post(base_url, payload, timeout):
        raise RuntimeError("gateway unreachable at http://x/v1/agent/run: refused")

    monkeypatch.setattr("client.terminal.post_request", fake_post)

    code = main(["--task", "Do it", "--target-repo", str(tmp_path), "--no-stream"])

    assert code == 2
    assert "gateway unreachable" in capsys.readouterr().err


def test_main_renders_unicode_observation_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "client.terminal.post_request",
        lambda *_: {
            "plan": {"goal": "Inspect", "steps": []},
            "status": "completed",
            "summary": "",
            "observations": [
                {"call": {"operation": "read", "path": "f.py"}, "succeeded": True, "output": "\ufeffcaf\u00e9"}
            ],
        },
    )

    code = main(["--task", "Inspect", "--target-repo", str(tmp_path), "--no-stream"])

    assert code == 0
    assert "caf\u00e9" in capsys.readouterr().out


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


def test_main_streams_live_events_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
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

    def fake_stream(base_url, payload, timeout, on_event):
        for event in events:
            on_event(event)
        return events[-1]

    monkeypatch.setattr("client.terminal.stream_request", fake_stream)
    monkeypatch.setattr(
        "client.terminal.post_request",
        lambda *_: pytest.fail("fallback POST must not run when streaming works"),
    )

    code = main(["--task", "Fix it", "--target-repo", str(tmp_path), "--base-url", "http://x"])

    assert code == 0
    output = capsys.readouterr().out
    assert "Task: Fix it" in output
    assert "filesystem read a.py: succeeded; content" in output
    assert "Status: completed" in output


def test_main_falls_back_to_plain_post_when_stream_is_unavailable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def unavailable(*_args, **_kwargs):
        raise StreamUnavailable("no streaming endpoint at http://x/v1/agent/run/stream")

    def fake_post(base_url, payload, timeout):
        return {"plan": {"goal": "G", "steps": []}, "status": "completed", "summary": "ok"}

    monkeypatch.setattr("client.terminal.stream_request", unavailable)
    monkeypatch.setattr("client.terminal.post_request", fake_post)

    code = main(["--task", "Do it", "--target-repo", str(tmp_path), "--base-url", "http://x"])

    assert code == 0
    assert "Status: completed" in capsys.readouterr().out


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
