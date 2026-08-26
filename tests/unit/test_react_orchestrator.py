import asyncio
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from app.config import ModelProvider, Settings
from app.contracts import AgentRequest, TaskStatus, TestResult
from app.models.base import ModelClient, ModelRequest, ModelResponse
from app.models.router import ModelRouter
from app.memory.store import SessionStore, SqliteSessionStore
from app.orchestrator.graph import ReActOrchestrator
from app.planner.service import DeterministicTaskPlanner
from app.prompts.builder import PromptBuilder


class SequencedFakeModel(ModelClient):
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=next(self._responses), model_name="fake")


def build_orchestrator(
    responses: list[str], max_iterations: int = 2, session_store: SessionStore | None = None
) -> ReActOrchestrator:
    settings = Settings.from_env({"AGENT_MODEL_PROVIDER": "anthropic"})
    router = ModelRouter(
        settings,
        {ModelProvider.ANTHROPIC: lambda _: SequencedFakeModel(responses)},
    )
    return ReActOrchestrator(
        planner=DeterministicTaskPlanner(),
        model_router=router,
        prompt_builder=PromptBuilder(),
        max_iterations=max_iterations,
        session_store=session_store,
    )


def test_react_graph_observes_a_tool_and_returns_final_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    orchestrator = build_orchestrator(
        [
            '{"kind":"tool_call","tool_name":"filesystem","operation":"read",'
            '"path":"README.md","arguments":{}}',
            '{"kind":"final","summary":"README inspected."}',
        ]
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path, apply_changes=False))
    )

    assert response.status is TaskStatus.COMPLETED
    assert response.summary == "README inspected."
    assert len(response.observations) == 1
    assert response.observations[0].output == "Project readme"


def test_react_graph_stops_at_the_action_limit(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Project readme", encoding="utf-8")
    orchestrator = build_orchestrator(
        [
            '{"kind":"tool_call","tool_name":"filesystem","operation":"read",'
            '"path":"README.md","arguments":{}}'
        ],
        max_iterations=1,
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path, apply_changes=False))
    )

    assert response.status is TaskStatus.FAILED
    assert response.summary == "Stopped after reaching the 1-action limit."
    assert len(response.observations) == 1


def test_react_graph_denies_changes_without_request_authorization(tmp_path: Path) -> None:
    orchestrator = build_orchestrator(
        [
            '{"kind":"tool_call","tool_name":"filesystem","operation":"create",'
            '"path":"notes.txt","arguments":{"content":"draft"}}',
            '{"kind":"final","summary":"Change was not applied."}',
        ]
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Create notes", target_repo=tmp_path, apply_changes=False))
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
            '{"kind":"tool_call","tool_name":"filesystem","operation":"write",'
            '"path":"notes.txt","arguments":{"content":"intermediate"}}',
            '{"kind":"tool_call","tool_name":"filesystem","operation":"write",'
            '"path":"notes.txt","arguments":{"content":"final"}}',
            '{"kind":"final","summary":"Tests pass after the correction."}',
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


def test_react_graph_persists_the_completed_session_summary(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent-state.sqlite3")
    orchestrator = build_orchestrator(
        ['{"kind":"final","summary":"README requires no changes."}'], session_store=store
    )

    response = asyncio.run(
        orchestrator.run(AgentRequest(task="Review README", target_repo=tmp_path))
    )

    assert response.session_id is not None
    session = store.get(response.session_id)
    assert session is not None
    assert session.task == "Review README"
    assert session.target_root == tmp_path.resolve()
    assert session.summary == "README requires no changes."
