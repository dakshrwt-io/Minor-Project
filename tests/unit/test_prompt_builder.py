import json
from pathlib import Path

from app.contracts import FilesystemOperation, TaskPlan, TaskStep, ToolCall, ToolResult
from app.memory.context import ObservationCompactor
from app.prompts.builder import PromptBuilder


def test_prompt_builder_includes_plan_observation_and_filesystem_schema(tmp_path: Path) -> None:
    plan = TaskPlan(goal="Update README", steps=[TaskStep(id="inspect", description="Read README")])
    observation = ToolResult(
        call=ToolCall(
            tool_name="filesystem",
            operation=FilesystemOperation.READ,
            path=Path("README.md"),
        ),
        succeeded=True,
        output="Current README",
    )

    request = PromptBuilder().build(
        plan=plan,
        target_root=tmp_path,
        apply_changes=False,
        observations=[observation],
    )

    assert request.messages[0].role == "user"
    assert '"goal": "Update README"' in request.messages[0].content
    assert "Current README" in request.messages[0].content
    assert '"name": "filesystem"' in request.system_prompt
    assert '"edit"' in request.system_prompt
    assert "Do not select create, write, or edit" in request.system_prompt


def test_prompt_builder_allows_changes_only_when_requested(tmp_path: Path) -> None:
    plan = TaskPlan(goal="Add a file", steps=[TaskStep(id="apply", description="Create a file")])

    request = PromptBuilder().build(
        plan=plan,
        target_root=tmp_path,
        apply_changes=True,
        observations=[],
    )

    assert "You may select create, write, or edit" in request.system_prompt


def test_prompt_builder_compacts_older_observations(tmp_path: Path) -> None:
    plan = TaskPlan(goal="Inspect files", steps=[TaskStep(id="inspect", description="Read files")])
    observations = [
        ToolResult(
            call=ToolCall(
                tool_name="filesystem", operation=FilesystemOperation.READ, path=Path(f"{index}.txt")
            ),
            succeeded=True,
            output=f"content {index}",
        )
        for index in range(3)
    ]
    builder = PromptBuilder(ObservationCompactor(max_recent_observations=1))

    request = builder.build(
        plan=plan, target_root=tmp_path, apply_changes=False, observations=observations
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["observation_summary"].startswith("Compacted 2 earlier observation(s):")
    assert context["recent_observations"][0]["output"] == "content 2"


def test_prompt_builder_includes_repository_summary(tmp_path: Path) -> None:
    plan = TaskPlan(goal="Inspect modules", steps=[TaskStep(id="inspect", description="Read files")])

    request = PromptBuilder().build(
        plan=plan,
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        repository_summary="Python repository index: 2 module(s), 1 internal import edge(s), 0 parse issue(s).",
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["repository_summary"].startswith("Python repository index: 2 module")
