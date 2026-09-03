"""AgentRunner checks: budget, repetition, streaming, sessions, and MCP isolation."""

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from agents.testing.model import ScriptedModel, assistant_message, function_call

from app.agent import AgentRunner
from app.config import McpServerConfig, Settings
from app.contracts import AgentRequest, ExternalToolResult, TestResult, ToolResult

_ECHO_MCP_SERVER = """\
\"\"\"Tiny stdio MCP echo server (official SDK v2).\"\"\"

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo-server")


@server.tool()
def echo(text: str) -> str:
    \"\"\"Echo the supplied text back, proving external tool execution.\"\"\"
    return f"echo: {text}"


if __name__ == "__main__":
    server.run(transport="stdio")
"""


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"AGENT_SESSION_DB": ":memory:", "AGENT_MAX_ITERATIONS": "6"}
    values.update(overrides)
    return Settings.from_env(values)  # type: ignore[arg-type]


def _repo(tmp_path: Path, file_name: str = "README.md", content: str = "Gateway demo") -> Path:
    (tmp_path / file_name).write_text(content, encoding="utf-8")
    return tmp_path


def test_budget_cutoff_fails_the_run_after_max_turns(tmp_path: Path) -> None:
    """A model that never stops calling tools is stopped by the action budget."""

    model = ScriptedModel(
        [
            [function_call("fs_read", {"path": f"f{index}.txt"}, call_id=f"c{index}")]
            for index in range(10)
        ]
    )
    runner = AgentRunner(_settings(AGENT_MAX_ITERATIONS="3"), model=model)

    response = asyncio.run(runner.run(AgentRequest(task="work", target_repo=_repo(tmp_path))))

    assert response.status.value == "failed"
    assert response.summary == "Stopped after reaching the 3-action limit."
    assert model.remaining_steps == 7  # exactly three turns were consumed


def test_repeated_identical_action_is_blocked_not_executed(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            [function_call("fs_read", {"path": "README.md"}, call_id="a")],
            [function_call("fs_read", {"path": "README.md"}, call_id="b")],
            [assistant_message("done")],
        ]
    )
    runner = AgentRunner(_settings(), model=model)

    response = asyncio.run(runner.run(AgentRequest(task="work", target_repo=_repo(tmp_path))))

    assert response.status.value == "completed"
    assert isinstance(response.observations[0], ToolResult)
    assert response.observations[0].succeeded
    assert isinstance(response.observations[1], ToolResult)
    assert "blocked: identical to the previous action" in (response.observations[1].error or "")


def test_run_events_stream_plan_action_observation_done(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            [function_call("fs_read", {"path": "README.md"}, call_id="c1")],
            [assistant_message("README reviewed.")],
        ]
    )
    runner = AgentRunner(_settings(), model=model)

    async def collect() -> list[dict[str, object]]:
        return [
            event
            async for event in runner.run_events(
                AgentRequest(task="review", target_repo=_repo(tmp_path))
            )
        ]

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == ["plan", "action", "observation", "done"]
    assert events[1]["name"] == "fs_read"
    assert events[2]["observation"]["output"] == "Gateway demo"
    assert events[-1]["status"] == "completed"
    assert events[-1]["summary"] == "README reviewed."
    assert events[-1]["response"]["observations"][0]["output"] == "Gateway demo"


def test_text_only_request_completes_without_observations(tmp_path: Path) -> None:
    model = ScriptedModel([[assistant_message("Hello! What should we build?")]])
    runner = AgentRunner(_settings(), model=model)

    response = asyncio.run(runner.run(AgentRequest(task="hello", target_repo=_repo(tmp_path))))

    assert response.status.value == "completed"
    assert response.summary == "Hello! What should we build?"
    assert response.observations == []


def test_read_only_run_advertises_only_inspection_tools(tmp_path: Path) -> None:
    model = ScriptedModel([[assistant_message("inspected")]])
    runner = AgentRunner(_settings(), model=model)

    asyncio.run(runner.run(AgentRequest(task="look", target_repo=_repo(tmp_path))))

    tool_names = sorted(tool.name for tool in model.calls[0].tools)  # type: ignore[attr-defined]
    assert tool_names == ["fs_list", "fs_read"]


def test_authorized_run_advertises_mutation_and_test_tools(tmp_path: Path) -> None:
    model = ScriptedModel([[assistant_message("ok")]])
    runner = AgentRunner(_settings(), model=model)

    asyncio.run(
        runner.run(AgentRequest(task="work", target_repo=_repo(tmp_path), apply_changes=True))
    )

    tool_names = sorted(tool.name for tool in model.calls[0].tools)  # type: ignore[attr-defined]
    assert tool_names == ["fs_create", "fs_edit", "fs_list", "fs_read", "fs_write", "run_tests"]


def test_successful_mutation_auto_runs_the_configured_tests(tmp_path: Path) -> None:
    (tmp_path / ".coding-agent.toml").write_text(
        f"[test]\ncommand = {json.dumps([sys.executable, '-c', 'print(42)'])}\n",
        encoding="utf-8",
    )
    model = ScriptedModel(
        [
            [function_call("fs_create", {"path": "notes.txt", "content": "draft"}, call_id="k1")],
            [assistant_message("created")],
        ]
    )
    runner = AgentRunner(_settings(), model=model)

    response = asyncio.run(
        runner.run(AgentRequest(task="make file", target_repo=tmp_path, apply_changes=True))
    )

    assert [type(observation).__name__ for observation in response.observations] == [
        "ToolResult",
        "TestResult",
    ]
    tests = response.observations[1]
    assert isinstance(tests, TestResult)
    assert tests.passed
    assert "42" in tests.output


def test_session_replays_prior_turns_to_the_model(tmp_path: Path, tmp_path_factory) -> None:
    db_path = tmp_path_factory.mktemp("sessions") / "sessions.sqlite3"
    model = ScriptedModel(
        [
            [assistant_message("Hello!")],
            [assistant_message("You said hello earlier.")],
            [assistant_message("Nothing yet.")],
        ]
    )
    runner = AgentRunner(
        dataclasses.replace(_settings(), session_db_path=str(db_path)), model=model
    )

    first = asyncio.run(
        runner.run(AgentRequest(task="hello", target_repo=_repo(tmp_path), session_id="repl-7"))
    )
    second = asyncio.run(
        runner.run(
            AgentRequest(task="what did I say", target_repo=_repo(tmp_path), session_id="repl-7")
        )
    )
    other = asyncio.run(
        runner.run(
            AgentRequest(task="what did I say", target_repo=_repo(tmp_path), session_id="repl-9")
        )
    )

    assert first.summary == "Hello!"
    assert second.summary == "You said hello earlier."
    assert (
        other.summary == "Nothing yet."
    )  # scripted; the input-length assertions are the real check
    first_input = model.calls[0].input
    second_input = model.calls[1].input
    other_input = model.calls[2].input
    assert isinstance(second_input, list) and len(second_input) > len(first_input)
    assert isinstance(other_input, list) and len(other_input) == len(first_input)
    replayed = "".join(
        str(item.get("content", "")) for item in second_input if isinstance(item, dict)
    )
    assert "hello" in replayed


def test_mcp_call_executes_and_a_failed_server_does_not_block_others(
    tmp_path: Path, tmp_path_factory: Path
) -> None:
    server_dir = tmp_path_factory.mktemp("mcp")
    echo_server = server_dir / "echo_server.py"
    echo_server.write_text(_ECHO_MCP_SERVER, encoding="utf-8")
    broken_server = server_dir / "definitely_missing_server.py"
    servers = (
        McpServerConfig(name="demo", command=sys.executable, args=[str(echo_server)]),
        McpServerConfig(name="broken", command=sys.executable, args=[str(broken_server)]),
    )
    model = ScriptedModel(
        [
            [function_call("mcp_demo__echo", {"text": "ping"}, call_id="m1")],
            [assistant_message("echoed")],
        ]
    )
    runner = AgentRunner(dataclasses.replace(_settings(), mcp_servers=servers), model=model)

    async def run() -> object:
        return await runner.run(AgentRequest(task="echo", target_repo=_repo(tmp_path)))

    response = asyncio.run(run())  # type: ignore[arg-type]

    assert response.status.value == "completed"
    assert response.observations, "the MCP call must produce an auditable observation"
    observation = response.observations[0]
    assert isinstance(observation, ExternalToolResult)
    assert observation.succeeded
    assert observation.content == ("echo: ping",)


def test_streamed_run_without_tool_calls_emits_plan_and_done_only(tmp_path: Path) -> None:
    model = ScriptedModel([[assistant_message("Hi!")]])
    runner = AgentRunner(_settings(), model=model)

    async def collect() -> list[dict[str, object]]:
        return [
            event
            async for event in runner.run_events(
                AgentRequest(task="hello", target_repo=_repo(tmp_path))
            )
        ]

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == ["plan", "done"]
    assert events[0]["plan"]["steps"] == []
