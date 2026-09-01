from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.contracts import FilesystemOperation, TaskPlan, TaskStep, ToolCall, ToolResult


def test_settings_use_explicit_values() -> None:
    settings = Settings.from_env(
        {
            "ANTHROPIC_API_KEY": "test-key",
            "AGENT_MODEL": "test-model",
            "AGENT_MAX_ITERATIONS": "3",
        }
    )

    assert settings.model_name == "test-model"
    assert settings.max_agent_iterations == 3


def test_settings_reject_non_positive_iteration_limit() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Settings.from_env({"AGENT_MAX_ITERATIONS": "0"})


def test_tool_result_requires_error_for_failure() -> None:
    call = ToolCall(
        tool_name="filesystem",
        operation=FilesystemOperation.READ,
        path=Path("README.md"),
    )

    with pytest.raises(ValidationError, match="failed tool results"):
        ToolResult(call=call, succeeded=False)


def test_task_plan_preserves_ordered_steps() -> None:
    plan = TaskPlan(
        goal="Update the README",
        steps=[TaskStep(id="inspect", description="Read the existing README")],
    )

    assert plan.steps[0].id == "inspect"
