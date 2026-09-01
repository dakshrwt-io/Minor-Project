import asyncio

import pytest

from app.contracts import TaskPlan
from app.models.base import ModelClient, ModelRequest, ModelResponse, ModelToolCall
from app.planner.service import DeterministicTaskPlanner, ModelTaskPlanner


def test_deterministic_planner_creates_ordered_read_only_plan() -> None:
    plan = asyncio.run(
        DeterministicTaskPlanner().create_plan("Update README", apply_changes=False)
    )

    assert plan.goal == "Update README"
    assert [step.id for step in plan.steps] == ["inspect", "decide", "complete"]
    assert "Describe" in plan.steps[-1].description


def test_deterministic_planner_marks_final_step_as_applying_changes_when_requested() -> None:
    plan = asyncio.run(
        DeterministicTaskPlanner().create_plan("Update README", apply_changes=True)
    )

    assert "Apply" in plan.steps[-1].description


class StaticFakeModel(ModelClient):
    def __init__(self, response: ModelResponse) -> None:
        self._response = response

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return self._response


class FailingModel(ModelClient):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("provider request failed")


class RecordingFallback(DeterministicTaskPlanner):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_plan(
        self, task: str, *, apply_changes: bool, repository_summary: str = "", conversation=()
    ):
        self.calls.append(
            {
                "task": task,
                "apply_changes": apply_changes,
                "repository_summary": repository_summary,
                "conversation": conversation,
            }
        )
        return await super().create_plan(
            task,
            apply_changes=apply_changes,
            repository_summary=repository_summary,
            conversation=conversation,
        )


def _submit_plan_response(steps: list[dict], goal: str = "Fix README") -> ModelResponse:
    return ModelResponse(
        text="",
        model_name="fake",
        tool_calls=(ModelToolCall(id="t1", name="submit_plan", arguments={"goal": goal, "steps": steps}),),
    )


def test_model_planner_validates_a_submit_plan_call() -> None:
    model = StaticFakeModel(
        _submit_plan_response(
            [
                {"id": "read", "description": "Read the README"},
                {"id": "propose", "description": "Propose the minimal edit"},
            ]
        )
    )

    plan = asyncio.run(ModelTaskPlanner(lambda: model).create_plan("Fix README", apply_changes=False))

    assert plan.goal == "Fix README"
    assert [step.id for step in plan.steps] == ["read", "propose"]


def test_model_planner_sends_task_and_summary_to_the_model() -> None:
    captured: list[ModelRequest] = []

    class CapturingModel(StaticFakeModel):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return await super().complete(request)

    model = CapturingModel(_submit_plan_response([{"id": "s1", "description": "step"}]))

    asyncio.run(
        ModelTaskPlanner(lambda: model).create_plan(
            "Fix README", apply_changes=True, repository_summary="repo summary"
        )
    )

    assert [tool.name for tool in captured[0].tools] == ["submit_plan"]
    assert '"task": "Fix README"' in captured[0].messages[0].content
    assert '"apply_changes": true' in captured[0].messages[0].content
    assert "repo summary" in captured[0].messages[0].content
    assert '"conversation": []' in captured[0].messages[0].content


def test_model_planner_passes_prior_conversation_turns_to_the_model() -> None:
    captured: list[ModelRequest] = []

    class CapturingModel(StaticFakeModel):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return await super().complete(request)

    model = CapturingModel(_submit_plan_response([{"id": "s1", "description": "step"}]))
    history = [{"user": "create hello.py", "agent": "Created hello.py.", "route": "task"}]

    asyncio.run(
        ModelTaskPlanner(lambda: model).create_plan(
            "now add tests for it", apply_changes=True, conversation=history
        )
    )

    assert '"user": "create hello.py"' in captured[0].messages[0].content
    assert "conversation lists earlier turns" in captured[0].system_prompt


def test_model_planner_falls_back_when_the_model_replies_without_a_plan() -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(ModelResponse(text="I cannot plan that.", model_name="fake"))

    plan = asyncio.run(
        ModelTaskPlanner(lambda: model, fallback).create_plan("Fix README", apply_changes=False)
    )

    assert fallback.calls == [
        {
            "task": "Fix README",
            "apply_changes": False,
            "repository_summary": "",
            "conversation": (),
        }
    ]
    assert [step.id for step in plan.steps] == ["inspect", "decide", "complete"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"goal": "", "steps": [{"id": "s", "description": "d"}]},
        {"goal": "Fix README"},
        {"goal": "Fix README", "steps": []},
        {"goal": "Fix README", "steps": [{"id": "s"}]},
        {"goal": "Fix README", "steps": ["not-a-dict"]},
    ],
)
def test_model_planner_falls_back_on_unusable_plan_payloads(arguments: dict) -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(
        ModelResponse(
            text="",
            model_name="fake",
            tool_calls=(ModelToolCall(id="t1", name="submit_plan", arguments=arguments),),
        )
    )

    plan = asyncio.run(
        ModelTaskPlanner(lambda: model, fallback).create_plan("Fix README", apply_changes=False)
    )

    assert len(fallback.calls) == 1
    assert isinstance(plan, TaskPlan)


def test_model_planner_truncates_oversized_plans() -> None:
    steps = [{"id": f"step-{index}", "description": f"Step {index}"} for index in range(12)]
    model = StaticFakeModel(_submit_plan_response(steps))

    plan = asyncio.run(
        ModelTaskPlanner(lambda: model, max_plan_steps=4).create_plan("Fix README", apply_changes=False)
    )

    assert len(plan.steps) == 4


def test_model_planner_keeps_files_the_plan_is_grounded_in() -> None:
    model = StaticFakeModel(
        ModelResponse(
            text="",
            model_name="fake",
            tool_calls=(
                ModelToolCall(
                    id="t1",
                    name="submit_plan",
                    arguments={
                        "goal": "Fix README",
                        "relevant_files": ["README.md", "app/main.py", "README.md"],
                        "steps": [{"id": "edit", "description": "Edit README.md"}],
                    },
                ),
            ),
        )
    )

    plan = asyncio.run(ModelTaskPlanner(lambda: model).create_plan("Fix README", apply_changes=False))

    assert plan.relevant_files == ["README.md", "app/main.py"]
    assert plan.steps[0].description == "Edit README.md"


def test_model_planner_defaults_to_no_relevant_files() -> None:
    model = StaticFakeModel(_submit_plan_response([{"id": "s1", "description": "step"}]))

    plan = asyncio.run(ModelTaskPlanner(lambda: model).create_plan("Fix README", apply_changes=False))

    assert plan.relevant_files == []


@pytest.mark.parametrize("relevant_files", ["README.md", [1, 2], ["README.md", ""]])
def test_model_planner_falls_back_on_unusable_relevant_files(relevant_files: object) -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(
        ModelResponse(
            text="",
            model_name="fake",
            tool_calls=(
                ModelToolCall(
                    id="t1",
                    name="submit_plan",
                    arguments={
                        "goal": "Fix README",
                        "relevant_files": relevant_files,
                        "steps": [{"id": "s1", "description": "step"}],
                    },
                ),
            ),
        )
    )

    plan = asyncio.run(
        ModelTaskPlanner(lambda: model, fallback).create_plan("Fix README", apply_changes=False)
    )

    assert len(fallback.calls) == 1
    assert plan.relevant_files == []


def test_model_planner_prompts_for_evidence_based_planning() -> None:
    captured: list[ModelRequest] = []

    class CapturingModel(StaticFakeModel):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            captured.append(request)
            return await super().complete(request)

    model = CapturingModel(_submit_plan_response([{"id": "s1", "description": "step"}]))

    asyncio.run(
        ModelTaskPlanner(lambda: model).create_plan(
            "Fix README", apply_changes=False, repository_summary="repo summary"
        )
    )

    system_prompt = captured[0].system_prompt
    assert "repository_summary" in system_prompt
    assert "evidence" in system_prompt
    assert "relevant_files" in system_prompt
    schema = captured[0].tools[0].input_schema
    assert "relevant_files" in schema["properties"]


def test_model_planner_ignores_unrelated_tool_calls() -> None:
    fallback = RecordingFallback()
    model = StaticFakeModel(
        ModelResponse(
            text="",
            model_name="fake",
            tool_calls=(ModelToolCall(id="t1", name="fs_read", arguments={"path": "x"}),),
        )
    )

    asyncio.run(
        ModelTaskPlanner(lambda: model, fallback).create_plan("Fix README", apply_changes=False)
    )

    assert len(fallback.calls) == 1


def test_model_planner_lets_provider_errors_propagate() -> None:
    with pytest.raises(RuntimeError, match="provider request failed"):
        asyncio.run(
            ModelTaskPlanner(lambda: FailingModel()).create_plan("Fix README", apply_changes=False)
        )


def test_model_planner_lets_model_factory_configuration_errors_propagate() -> None:
    def broken_factory() -> ModelClient:
        raise ValueError("API key is required")

    fallback = RecordingFallback()
    with pytest.raises(ValueError, match="API key is required"):
        asyncio.run(
            ModelTaskPlanner(broken_factory, fallback).create_plan("Fix README", apply_changes=False)
        )

    assert fallback.calls == []
