import asyncio
import json
import sys
import threading
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from app.config import ModelProvider, Settings
from app.contracts import (
    AgentRequest,
    ExternalToolDefinition,
    ExternalToolResult,
    RouteDecision,
    TaskStatus,
    TestResult,
)
from app.intelligence.python_analyzer import PythonProjectAnalyzer
from app.mcp.adapter import McpClientAdapter
from app.mcp.connection import McpDiscovery, McpServerConfig, McpServerConnection
from app.memory.session import ConversationTurn, SessionStore
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.models.router import ModelRouter
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder
from app.router.service import DeterministicTaskRouter, TaskRouter


class SequencedFakeModel(ModelClient):
    def __init__(
        self, responses: Iterable[ModelResponse], requests: list[ModelRequest] | None = None
    ) -> None:
        self._responses = iter(responses)
        self._requests = requests

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._requests is not None:
            self._requests.append(request)
        return next(self._responses)


def tool_response(name: str, arguments: dict | None = None) -> ModelResponse:
    return ModelResponse(
        text="",
        model_name="fake",
        tool_calls=(ModelToolCall(id="t1", name=name, arguments=arguments or {}),),
    )


def final_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, model_name="fake")


class RecordingMcpAdapter(McpClientAdapter):
    def __init__(self, tool_names: list[str]) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self._tool_names = tool_names
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> list[ExternalToolDefinition]:
        return [
            ExternalToolDefinition(
                name=name, description=f"Tool {name}.", input_schema={"type": "object"}
            )
            for name in self._tool_names
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> ExternalToolResult:
        self.calls.append((tool_name, arguments))
        return ExternalToolResult(tool_name=tool_name, succeeded=True, content=("found",))


def make_opener(adapters: list[McpServerConnection], errors: list[str] | None = None):
    @asynccontextmanager
    async def opener(_: list[McpServerConfig]) -> AsyncIterator[McpDiscovery]:
        yield McpDiscovery(connections=adapters, errors=errors or [])

    return opener


def build_orchestrator(
    responses: list[ModelResponse],
    max_iterations: int = 2,
    requests: list[ModelRequest] | None = None,
    mcp_servers: list[McpServerConfig] | None = None,
    external_tool_opener=None,
    repository_analyzer: PythonProjectAnalyzer | None = None,
    task_router: TaskRouter | None = None,
    session_store=None,
) -> ReActOrchestrator:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {ModelProvider.ANTHROPIC: lambda _: SequencedFakeModel(responses, requests)},
    )
    return ReActOrchestrator(
        task_router=task_router or DeterministicTaskRouter(),
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=max_iterations,
        mcp_servers=mcp_servers or [],
        external_tool_opener=external_tool_opener,
        repository_analyzer=repository_analyzer,
        session_store=session_store,
    )


def test_react_graph_observes_a_tool_and_returns_final_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    orchestrator = build_orchestrator(
        [
            tool_response("fs_read", {"path": "README.md"}),
            final_response("README inspected."),
        ]
    )

    response = asyncio.run(
        orchestrator.run(
        AgentRequest(task="Review README", target_repo=tmp_path, apply_changes=False)
    )
    )

    assert response.status is TaskStatus.COMPLETED
    assert response.summary == "README inspected."
    assert len(response.observations) == 1
    assert response.observations[0].output == "Project readme"


def test_react_graph_stops_at_the_action_limit(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    orchestrator = build_orchestrator(
        [tool_response("fs_read", {"path": "README.md"})],
        max_iterations=1,
    )

    response = asyncio.run(
        orchestrator.run(
        AgentRequest(task="Review README", target_repo=tmp_path, apply_changes=False)
    )
    )

    assert response.status is TaskStatus.FAILED
    assert response.summary == "Stopped after reaching the 1-action limit."
    assert len(response.observations) == 1


def test_react_graph_emits_live_progress_events(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    orchestrator = build_orchestrator(
        [
            tool_response("fs_read", {"path": "README.md"}),
            final_response("README inspected."),
        ]
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path), emit=emit)
    )

    assert response.status is TaskStatus.COMPLETED
    assert [event["type"] for event in events] == ["plan", "action", "observation"]
    assert events[0]["session_id"] == response.session_id
    assert events[0]["plan"]["goal"] == "Review README"
    assert events[1]["name"] == "fs_read"
    assert events[1]["arguments"] == {"path": "README.md"}
    assert events[2]["observation"]["output"] == "Project readme"
    assert events[2]["remaining"] == 1


def test_react_graph_blocks_a_repeated_identical_action(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    orchestrator = build_orchestrator(
        [
            tool_response("fs_read", {"path": "README.md"}),
            tool_response("fs_read", {"path": "README.md"}),
            final_response("Finished after the block."),
        ],
        max_iterations=3,
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path))
    )

    assert response.status is TaskStatus.COMPLETED
    assert len(response.observations) == 2
    first, second = response.observations
    assert first.succeeded and first.output == "Project readme"
    assert not second.succeeded
    assert "identical to the previous action" in second.error


def test_react_graph_tells_the_model_its_remaining_action_budget(tmp_path: Path) -> None:
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator(
        [
            tool_response("fs_read", {"path": "README.md"}),
            final_response("Done."),
        ],
        max_iterations=4,
        requests=requests,
    )
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")

    asyncio.run(orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path)))

    first_context = json.loads(
        requests[0].messages[0].content.removeprefix("Current agent context:\n")
    )
    second_context = json.loads(
        requests[1].messages[0].content.removeprefix("Current agent context:\n")
    )
    assert first_context["action_budget"] == {"limit": 4, "used": 0, "remaining": 4}
    assert second_context["action_budget"] == {"limit": 4, "used": 1, "remaining": 3}


def test_react_graph_advertises_only_inspection_tools_without_authorization(
    tmp_path: Path,
) -> None:
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator([final_response("Inspected.")], requests=requests)

    asyncio.run(
        orchestrator.run(
            AgentRequest(task="Inspect", target_repo=tmp_path, apply_changes=False)
        )
    )

    assert [tool.name for tool in requests[0].tools] == ["fs_list", "fs_read"]


def test_react_graph_advertises_mutation_tools_with_authorization(tmp_path: Path) -> None:
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator([final_response("Done.")], requests=requests)

    asyncio.run(
        orchestrator.run(AgentRequest(task="Change", target_repo=tmp_path, apply_changes=True))
    )

    assert [tool.name for tool in requests[0].tools] == [
        "fs_list",
        "fs_read",
        "fs_create",
        "fs_write",
        "fs_edit",
    ]


def test_react_graph_fails_on_two_consecutive_empty_model_replies(tmp_path: Path) -> None:
    orchestrator = build_orchestrator(
        [ModelResponse(text="   ", model_name="fake"), ModelResponse(text="", model_name="fake")]
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Inspect", target_repo=tmp_path, apply_changes=False))
    )

    assert response.status is TaskStatus.FAILED
    assert response.summary == "Model returned an empty response"


def test_react_graph_retries_once_after_a_transient_empty_reply(tmp_path: Path) -> None:
    orchestrator = build_orchestrator(
        [ModelResponse(text="", model_name="fake"), final_response("Recovered from a blank reply.")]
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Inspect", target_repo=tmp_path, apply_changes=False))
    )

    assert response.status is TaskStatus.COMPLETED
    assert response.summary == "Recovered from a blank reply."
    assert response.observations == []


def test_react_graph_denies_changes_without_request_authorization(tmp_path: Path) -> None:
    orchestrator = build_orchestrator(
        [
            tool_response("fs_create", {"path": "notes.txt", "content": "draft"}),
            final_response("Change was not applied."),
        ]
    )

    response = asyncio.run(
        orchestrator.run(
        AgentRequest(task="Create notes", target_repo=tmp_path, apply_changes=False)
    )
    )

    assert response.status is TaskStatus.COMPLETED
    assert not response.observations[0].succeeded
    assert response.observations[0].error == (
        "permission denied: create operation requires apply_changes=True on the agent request"
    )
    assert not (tmp_path / "notes.txt").exists()


def test_react_graph_observes_test_failures_and_retries_within_action_limit(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("draft", encoding="utf-8")
    check_content = (
        "from pathlib import Path; import sys; "
        "sys.exit(0 if Path('notes.txt').read_text() == 'final' else 1)"
    )
    (tmp_path / ".coding-agent.toml").write_text(
        "[test]\ncommand = " + json.dumps([sys.executable, "-c", check_content]) + "\n",
        encoding="utf-8",
    )
    orchestrator = build_orchestrator(
        [
            tool_response("fs_write", {"path": "notes.txt", "content": "intermediate"}),
            tool_response("fs_write", {"path": "notes.txt", "content": "final"}),
            final_response("Tests pass after the correction."),
        ],
        max_iterations=3,
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Fix notes", target_repo=tmp_path, apply_changes=True))
    )

    test_results = [item for item in response.observations if isinstance(item, TestResult)]
    assert response.status is TaskStatus.COMPLETED
    assert [result.passed for result in test_results] == [False, True]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "final"


def test_react_graph_mints_a_fresh_session_for_every_run(tmp_path: Path) -> None:
    requests: list[ModelRequest] = []
    first = build_orchestrator([final_response("First run done.")], requests=requests)
    second = build_orchestrator([final_response("Second run done.")], requests=requests)

    first_response = asyncio.run(
        first.run(AgentRequest(task="First task", target_repo=tmp_path))
    )
    second_response = asyncio.run(
        second.run(AgentRequest(task="Second task", target_repo=tmp_path))
    )

    assert first_response.session_id is not None
    assert second_response.session_id is not None
    assert first_response.session_id != second_response.session_id
    context = json.loads(requests[1].messages[0].content.removeprefix("Current agent context:\n"))
    assert "prior_sessions" not in context


def test_react_orchestrator_runs_blocking_work_off_the_event_loop(tmp_path: Path) -> None:
    main_thread = threading.get_ident()
    blocking_threads: list[int] = []

    class RecordingAnalyzer(PythonProjectAnalyzer):
        def analyze(self, target_root: Path):
            blocking_threads.append(threading.get_ident())
            return super().analyze(target_root)

    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    orchestrator = build_orchestrator(
        [final_response("Done.")],
        repository_analyzer=RecordingAnalyzer(),
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Inspect repository", target_repo=tmp_path))
    )

    assert response.status is TaskStatus.COMPLETED
    assert blocking_threads
    assert all(thread != main_thread for thread in blocking_threads)


def test_react_graph_includes_a_python_repository_summary_in_prompt(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator([final_response("Repository inspected.")], requests=requests)

    asyncio.run(orchestrator.run(AgentRequest(task="Inspect repository", target_repo=tmp_path)))

    context = json.loads(requests[0].messages[0].content.removeprefix("Current agent context:\n"))
    assert "main (main.py): function run" in context["repository_summary"]


def test_react_graph_advertises_discovered_mcp_tools_in_the_prompt(tmp_path: Path) -> None:
    requests: list[ModelRequest] = []
    adapter = RecordingMcpAdapter(["docs.search_docs"])
    orchestrator = build_orchestrator(
        [final_response("Done.")],
        requests=requests,
        mcp_servers=[McpServerConfig(name="docs", command="python", args=["-m", "docs"])],
        external_tool_opener=make_opener(
            [McpServerConnection(server_name="docs", adapter=adapter)],
            errors=["server broken: refused to start"],
        ),
    )

    asyncio.run(orchestrator.run(AgentRequest(task="Search docs", target_repo=tmp_path)))

    assert [tool.name for tool in requests[0].tools if tool.name.startswith("fs_")] == [
        "fs_list",
        "fs_read",
    ]
    assert any(tool.name == "docs.search_docs" for tool in requests[0].tools)
    context = json.loads(requests[0].messages[0].content.removeprefix("Current agent context:\n"))
    assert context["external_tool_errors"] == ["server broken: refused to start"]


def test_react_graph_executes_a_model_issued_mcp_call(tmp_path: Path) -> None:
    adapter = RecordingMcpAdapter(["docs.search_docs"])
    orchestrator = build_orchestrator(
        [
            tool_response("docs.search_docs", {"query": "MCP"}),
            final_response("Search completed."),
        ],
        mcp_servers=[McpServerConfig(name="docs", command="python", args=[])],
        external_tool_opener=make_opener(
            [McpServerConnection(server_name="docs", adapter=adapter)]
        ),
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Search docs", target_repo=tmp_path))
    )

    assert response.status is TaskStatus.COMPLETED
    assert adapter.calls == [("docs.search_docs", {"query": "MCP"})]
    assert len(response.observations) == 1
    observation = response.observations[0]
    assert isinstance(observation, ExternalToolResult)
    assert observation.succeeded
    assert observation.tool_name == "docs.search_docs"
    assert observation.content == ("found",)


def test_react_graph_rejects_an_unadvertised_external_tool(tmp_path: Path) -> None:
    adapter = RecordingMcpAdapter(["docs.search_docs"])
    orchestrator = build_orchestrator(
        [
            tool_response("docs.delete_everything"),
            final_response("Call rejected."),
        ],
        mcp_servers=[McpServerConfig(name="docs", command="python", args=[])],
        external_tool_opener=make_opener(
            [McpServerConnection(server_name="docs", adapter=adapter)]
        ),
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Search docs", target_repo=tmp_path))
    )

    assert adapter.calls == []
    observation = response.observations[0]
    assert isinstance(observation, ExternalToolResult)
    assert not observation.succeeded
    assert observation.error == (
        "unknown external tool 'docs.delete_everything' is not advertised"
    )


class StaticTaskRouter(TaskRouter):
    def __init__(self, decision: RouteDecision) -> None:
        self._decision = decision
        self.calls: list[dict] = []

    async def route(
        self, task: str, *, repository_summary: str = "", conversation=()
    ) -> RouteDecision:
        self.calls.append(
            {"task": task, "repository_summary": repository_summary, "conversation": conversation}
        )
        return self._decision


def test_react_graph_answers_chat_without_planner_or_loop(tmp_path: Path) -> None:
    class ExplodingPlanner(DeterministicTaskPlanner):
        async def create_plan(self, *args: object, **kwargs: object):
            raise AssertionError("the planner must not run for a chat message")

    router = StaticTaskRouter(RouteDecision(route="chat", reply="Hey! What should we build?"))
    orchestrator = build_orchestrator([], task_router=router)
    orchestrator._planner = ExplodingPlanner()

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="hello", target_repo=tmp_path, apply_changes=False))
    )

    assert router.calls[0]["task"] == "hello"
    assert "repository_summary" in router.calls[0]
    assert response.status is TaskStatus.COMPLETED
    assert response.summary == "Hey! What should we build?"
    assert response.observations == []
    assert response.plan.steps == []


def test_react_graph_chat_emits_only_a_plan_event(tmp_path: Path) -> None:
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    router = StaticTaskRouter(RouteDecision(route="chat", reply="Hi there!"))
    orchestrator = build_orchestrator([], task_router=router)

    asyncio.run(orchestrator.run(AgentRequest(task="hello", target_repo=tmp_path), emit=emit))

    assert [event["type"] for event in events] == ["plan"]
    assert events[0]["plan"]["steps"] == []


def test_react_graph_honors_a_client_supplied_session_id(tmp_path: Path) -> None:
    router = StaticTaskRouter(RouteDecision(route="chat", reply="Hey!"))
    orchestrator = build_orchestrator([], task_router=router)

    response = asyncio.run(
        orchestrator.run(
            AgentRequest(task="hello", target_repo=tmp_path, session_id="repl-session-1")
        )
    )

    assert response.session_id == "repl-session-1"


def test_react_graph_mints_a_session_id_when_the_client_supplies_none(tmp_path: Path) -> None:
    orchestrator = build_orchestrator([final_response("done")])

    response = asyncio.run(orchestrator.run(AgentRequest(task="hello", target_repo=tmp_path)))

    assert response.session_id


def test_react_graph_shows_prior_chat_turns_to_the_next_request(tmp_path: Path) -> None:
    store = SessionStore()
    router = StaticTaskRouter(RouteDecision(route="chat", reply="Hello again!"))
    orchestrator = build_orchestrator([], task_router=router, session_store=store)
    request = AgentRequest(task="hello", target_repo=tmp_path, session_id="s-chat")

    asyncio.run(orchestrator.run(request))
    follow_up = request.model_dump()
    follow_up["task"] = "what is my name"
    asyncio.run(orchestrator.run(AgentRequest(**follow_up)))

    first, second = router.calls
    assert first["conversation"] == []
    assert second["conversation"] == [
        {"user": "hello", "agent": "Hello again!", "route": "chat"}
    ]
    assert second["task"] == "what is my name"


def test_react_graph_shows_prior_task_turns_and_loop_history(tmp_path: Path) -> None:
    store = SessionStore()
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator(
        [final_response("Created hello.py and verified it.")],
        requests=requests,
        session_store=store,
    )

    first = asyncio.run(
        orchestrator.run(
            AgentRequest(task="create hello.py", target_repo=tmp_path, session_id="s1")
        )
    )
    router = StaticTaskRouter(RouteDecision(route="chat", reply="ok"))
    second_orchestrator = build_orchestrator([], task_router=router, session_store=store)
    asyncio.run(
        second_orchestrator.run(
            AgentRequest(task="status update?", target_repo=tmp_path, session_id="s1")
        )
    )

    assert first.status is TaskStatus.COMPLETED
    assert router.calls[0]["conversation"] == [
        {"user": "create hello.py", "agent": first.summary, "route": "task"}
    ]
    loop_context = json.loads(
        requests[0].messages[0].content.removeprefix("Current agent context:\n")
    )
    assert loop_context["conversation_history"] == []


def test_react_graph_includes_prior_turns_in_the_loop_prompt(tmp_path: Path) -> None:
    store = SessionStore()
    store.record(
        "s2",
        ConversationTurn(message="earlier task", reply="earlier summary", route="task"),
    )
    requests: list[ModelRequest] = []
    orchestrator = build_orchestrator(
        [final_response("done")], requests=requests, session_store=store
    )

    asyncio.run(
        orchestrator.run(AgentRequest(task="follow-up", target_repo=tmp_path, session_id="s2"))
    )

    context = json.loads(
        requests[0].messages[0].content.removeprefix("Current agent context:\n")
    )
    assert context["conversation_history"] == [
        {"user": "earlier task", "agent": "earlier summary", "route": "task"}
    ]
    assert "conversation_history holds earlier turns" in requests[0].system_prompt
