import json
from pathlib import Path

from app.contracts import (
    ExternalToolDefinition,
    FilesystemOperation,
    TaskPlan,
    TaskStep,
    ToolCall,
    ToolResult,
)
from app.memory.context import ObservationCompactor
from app.prompts.builder import PromptBuilder


def _plan() -> TaskPlan:
    return TaskPlan(goal="Inspect", steps=[TaskStep(id="inspect", description="Inspect")])


def test_prompt_builder_includes_plan_observation_and_context(tmp_path: Path) -> None:
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
    assert [tool.name for tool in request.tools] == ["fs_list", "fs_read"]


def test_prompt_builder_advertises_mutation_tools_only_when_authorized(tmp_path: Path) -> None:
    request = PromptBuilder().build(
        plan=_plan(), target_root=tmp_path, apply_changes=True, observations=[]
    )

    assert [tool.name for tool in request.tools] == [
        "fs_list",
        "fs_read",
        "fs_create",
        "fs_write",
        "fs_edit",
    ]


def test_prompt_builder_requires_required_arguments_in_mutation_schemas(tmp_path: Path) -> None:
    request = PromptBuilder().build(
        plan=_plan(), target_root=tmp_path, apply_changes=True, observations=[]
    )

    schemas = {tool.name: tool.input_schema for tool in request.tools}
    assert schemas["fs_create"]["required"] == ["path", "content"]
    assert schemas["fs_edit"]["required"] == ["path", "old_text", "new_text"]
    assert "required" not in schemas["fs_list"]


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
    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        repository_summary="Python repository index: 2 module(s), 1 internal import edge(s), 0 parse issue(s).",
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["repository_summary"].startswith("Python repository index: 2 module")


def _external_tool(name: str) -> ExternalToolDefinition:
    return ExternalToolDefinition(
        name=name,
        description=f"Tool {name}.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )


def test_prompt_builder_advertises_external_tools_as_native_tools(tmp_path: Path) -> None:
    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        external_tools=[_external_tool("search_docs")],
    )

    external = [tool for tool in request.tools if not tool.name.startswith("fs_")]
    assert [tool.name for tool in external] == ["search_docs"]
    assert external[0].description == "Tool search_docs."
    assert external[0].input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    assert "External tools are advertised alongside the filesystem tools" in request.system_prompt


def test_prompt_builder_bounds_the_advertised_tool_count(tmp_path: Path) -> None:
    request = PromptBuilder(max_external_tools=2).build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        external_tools=[_external_tool(f"tool_{index}") for index in range(4)],
    )

    advertised = [tool.name for tool in request.tools if tool.name.startswith("tool_")]
    assert advertised == ["tool_0", "tool_1"]


def test_prompt_builder_bounds_oversized_external_schemas(tmp_path: Path) -> None:
    oversized = ExternalToolDefinition(
        name="huge",
        description="A huge tool.",
        input_schema={"type": "object", "properties": {str(i): {"type": "string"} for i in range(500)}},
    )

    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        external_tools=[oversized],
    )

    tool = request.tools[-1]
    assert tool.name == "huge"
    assert tool.input_schema["properties"] == {}
    assert "exceeds the advertisement size limit" in tool.input_schema["description"]


def test_prompt_builder_reports_external_tool_errors(tmp_path: Path) -> None:
    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        external_tool_errors=["server docs: could not start"],
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["external_tool_errors"] == ["server docs: could not start"]


def test_prompt_builder_includes_the_action_budget_and_loop_discipline(tmp_path: Path) -> None:
    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        max_iterations=6,
        iterations_used=2,
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["action_budget"] == {"limit": 6, "used": 2, "remaining": 4}
    assert context["conversation_history"] == []
    system_prompt = request.system_prompt
    assert "Act when ready" in system_prompt
    assert "Never repeat an identical action" in system_prompt
    assert "stop calling tools and reply with plain text" in system_prompt
    assert "no shell, deletion, or network capabilities" in system_prompt
    assert "conversation_history holds earlier turns" not in system_prompt


def test_prompt_builder_carries_prior_conversation_turns(tmp_path: Path) -> None:
    history = [{"user": "create hello.py", "agent": "Created hello.py.", "route": "task"}]

    request = PromptBuilder().build(
        plan=_plan(),
        target_root=tmp_path,
        apply_changes=False,
        observations=[],
        conversation_history=history,
    )

    context = json.loads(request.messages[0].content.removeprefix("Current agent context:\n"))
    assert context["conversation_history"] == history
    assert "conversation_history holds earlier turns" in request.system_prompt
