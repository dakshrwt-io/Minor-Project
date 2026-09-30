"""Tests for the agent loop, using a fake model so no API key or internet is needed."""

import json
from types import SimpleNamespace

import pytest

from app import config
from app.agent import run_agent


def tool_call(name, arguments):
    """A fake 'the model wants to call a tool' reply."""
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(content="", tool_calls=[call])


def text_reply(text):
    """A fake 'the model answers in text' reply."""
    return SimpleNamespace(content=text, tool_calls=None)


class FakeClient:
    """Pretends to be the OpenAI client. Returns the scripted replies in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, model, messages, tools):
        reply = self.replies.pop(0)
        # Every fake call "uses" 100 input tokens, 10 output tokens and costs $0.001.
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, cost=0.001)
        return SimpleNamespace(choices=[SimpleNamespace(message=reply)], usage=usage)


async def collect(generator):
    return [event async for event in generator]


def usage_for(calls):
    """The usage totals we expect after this many fake model calls."""
    return {
        "model_calls": calls,
        "input_tokens": 100 * calls,
        "output_tokens": 10 * calls,
        "cost": pytest.approx(0.001 * calls),  # approx: decimals like 0.1 + 0.2 are not exact
    }


async def test_text_answer_finishes_immediately(tmp_path):
    client = FakeClient([text_reply("Hi there!")])

    events = await collect(run_agent("hello", str(tmp_path), False, [], client))

    assert events == [{"type": "done", "summary": "Hi there!", "usage": usage_for(1)}]


async def test_agent_calls_tool_then_answers(tmp_path):
    (tmp_path / "README.md").write_text("Demo project", encoding="utf-8")
    client = FakeClient(
        [
            tool_call("read_file", {"path": "README.md"}),
            text_reply("The README says: Demo project"),
        ]
    )
    messages = []

    events = await collect(run_agent("read the readme", str(tmp_path), False, messages, client))

    assert [event["type"] for event in events] == ["action", "observation", "done"]
    assert events[1]["result"] == "Demo project"
    # The tool result was added to the conversation so the model could see it.
    assert messages[-2] == {"role": "tool", "tool_call_id": "call_1", "content": "Demo project"}


async def test_agent_edits_file_when_allowed(tmp_path):
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    client = FakeClient(
        [
            tool_call("edit_file", {"path": "a.txt", "old_text": "old", "new_text": "new"}),
            text_reply("Done"),
        ]
    )

    await collect(run_agent("change old to new", str(tmp_path), True, [], client))

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


async def test_agent_stops_at_step_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_STEPS", 2)
    client = FakeClient([tool_call("list_files", {"path": "."})] * 2)

    events = await collect(run_agent("loop forever", str(tmp_path), False, [], client))

    summary = "Stopped after 2 steps without finishing."
    assert events[-1] == {"type": "done", "summary": summary, "usage": usage_for(2)}


async def test_usage_is_added_up_over_calls(tmp_path):
    client = FakeClient([tool_call("list_files", {"path": "."}), text_reply("Done")])

    events = await collect(run_agent("list files", str(tmp_path), False, [], client))

    assert events[-1]["usage"] == usage_for(2)
