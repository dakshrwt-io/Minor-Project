import json
from pathlib import Path

from agents.testing.model import ScriptedModel, assistant_message, function_call
from fastapi.testclient import TestClient

from app.agent import AgentRunner
from app.config import Settings
from app.main import create_app


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"AGENT_SESSION_DB": ":memory:", "AGENT_MAX_ITERATIONS": "4"}
    values.update(overrides)
    return Settings.from_env(values)  # type: ignore[arg-type]


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("Gateway demo", encoding="utf-8")
    return tmp_path


def _stream_events(client: TestClient, target_repo: Path, **overrides: object) -> list[dict]:
    body: dict[str, object] = {
        "task": "Review the README",
        "target_repo": str(target_repo),
        "apply_changes": False,
    }
    body.update(overrides)
    with client.stream("POST", "/v1/agent/run/stream", json=body) as response:
        assert response.status_code == 200
        return [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]


def test_gateway_runs_agent_against_explicit_target_repository(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            [function_call("fs_read", {"path": "README.md"}, call_id="c1")],
            [assistant_message("README reviewed.")],
        ]
    )
    client = TestClient(create_app(AgentRunner(_settings(), model=model)))

    events = _stream_events(client, _make_repo(tmp_path))

    assert events[0]["type"] == "plan"
    assert events[0]["plan"]["goal"] == "Review the README"
    assert events[1]["type"] == "action"
    assert events[1]["name"] == "fs_read"
    assert events[2]["type"] == "observation"
    assert events[2]["observation"]["output"] == "Gateway demo"
    done = events[-1]
    assert done["type"] == "done"
    assert done["status"] == "completed"
    assert done["summary"] == "README reviewed."
    assert done["response"]["observations"][0]["output"] == "Gateway demo"


def test_gateway_stream_endpoint_emits_live_sse_events(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            [function_call("fs_read", {"path": "README.md"}, call_id="c1")],
            [assistant_message("README reviewed.")],
        ]
    )
    client = TestClient(create_app(AgentRunner(_settings(), model=model)))

    with client.stream(
        "POST",
        "/v1/agent/run/stream",
        json={
            "task": "Review the README",
            "target_repo": str(_make_repo(tmp_path)),
            "apply_changes": False,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [event["type"] for event in events] == ["plan", "action", "observation", "done"]
    assert events[1]["name"] == "fs_read"
    assert events[2]["observation"]["output"] == "Gateway demo"
    done = events[-1]
    assert done["status"] == "completed"
    assert done["summary"] == "README reviewed."
    assert done["response"]["observations"][0]["output"] == "Gateway demo"


def test_gateway_answers_plain_conversation_without_tool_events(tmp_path: Path) -> None:
    model = ScriptedModel([[assistant_message("Hey! What should we build?")]])
    client = TestClient(create_app(AgentRunner(_settings(), model=model)))

    with client.stream(
        "POST",
        "/v1/agent/run/stream",
        json={"task": "hello", "target_repo": str(tmp_path), "apply_changes": False},
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [event["type"] for event in events] == ["plan", "done"]
    assert events[0]["plan"]["steps"] == []
    done = events[-1]
    assert done["status"] == "completed"
    assert done["summary"] == "Hey! What should we build?"
    assert done["response"]["observations"] == []


def test_gateway_keeps_sessions_isolated_and_persisted(tmp_path: Path, tmp_path_factory) -> None:
    db_path = tmp_path_factory.mktemp("sessions") / "sessions.sqlite3"
    model = ScriptedModel(
        [
            [assistant_message("Hello!")],
            [assistant_message("Hello again!")],
        ]
    )
    client = TestClient(
        create_app(AgentRunner(_settings(AGENT_SESSION_DB=str(db_path)), model=model))
    )

    first = _stream_events(client, tmp_path, session_id="repl-42")
    second = _stream_events(client, tmp_path, task="what is my name")

    assert first[-1]["response"]["session_id"] == "repl-42"
    assert second[-1]["response"]["session_id"] != "repl-42"  # no id supplied: fresh session


def test_gateway_stream_reports_provider_configuration_errors(tmp_path: Path) -> None:
    client = TestClient(create_app(AgentRunner(_settings(ANTHROPIC_API_KEY=""))))

    with client.stream(
        "POST",
        "/v1/agent/run/stream",
        json={"task": "hello", "target_repo": str(tmp_path), "apply_changes": False},
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[len("data: ") :])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert events[-1]["type"] == "error"
    assert events[-1]["status_code"] == 400
    assert "ANTHROPIC_API_KEY" in events[-1]["detail"]
