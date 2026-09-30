"""Tests for the web API, with a fake model and a temporary memory database."""

import json

from fastapi.testclient import TestClient

from app import agent, config, memory
from app.main import app
from tests.test_agent import FakeClient, text_reply


def post(task, repo, session_id="s1"):
    """Send one message to /run and return the list of events."""
    body = {"task": task, "target_repo": str(repo), "session_id": session_id}
    with TestClient(app) as client:
        response = client.post("/run", json=body)
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def setup(tmp_path, monkeypatch, replies):
    monkeypatch.setattr(config, "MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr(config, "MCP_SERVERS", [])
    fake = FakeClient(replies)
    monkeypatch.setattr(agent, "make_client", lambda: fake)


def test_run_streams_answer(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [text_reply("Hello!")])

    events = post("hi", tmp_path)

    assert events == [{"type": "done", "summary": "Hello!"}]


def test_chat_is_remembered_between_messages(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [text_reply("Nice to meet you"), text_reply("You are Daksh")])

    post("my name is Daksh", tmp_path)
    post("what is my name?", tmp_path)

    saved = memory.load_messages("s1")
    user_messages = [m["content"] for m in saved if m["role"] == "user"]
    assert len(user_messages) == 2
    assert user_messages[0].startswith("my name is Daksh")


def test_missing_folder_gives_error(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [])

    events = post("hi", tmp_path / "does-not-exist")

    assert events[0]["type"] == "error"
    assert "Folder not found" in events[0]["message"]


def test_empty_folder_path_gives_error(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [])

    events = post("hi", "")

    assert events[0]["type"] == "error"
