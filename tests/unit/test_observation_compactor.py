from pathlib import Path

import pytest

from app.contracts import FilesystemOperation, ToolCall, ToolResult
from app.memory.context import ObservationCompactor


def make_observation(index: int) -> ToolResult:
    return ToolResult(
        call=ToolCall(
            tool_name="filesystem", operation=FilesystemOperation.READ, path=Path(f"{index}.txt")
        ),
        succeeded=True,
        output=f"output {index}",
    )


def test_compactor_keeps_recent_observations_and_summarizes_older_ones() -> None:
    result = ObservationCompactor(max_recent_observations=2).compact(
        [make_observation(index) for index in range(4)]
    )

    assert "Compacted 2 earlier observation(s)" in result.summary
    assert "output 0" in result.summary
    assert [item.output for item in result.recent_observations] == ["output 2", "output 3"]


def test_compactor_bounds_the_summary_length() -> None:
    observation = make_observation(0).model_copy(update={"output": "a" * 500})

    result = ObservationCompactor(max_recent_observations=1, max_summary_characters=80).compact(
        [observation, make_observation(1)]
    )

    assert len(result.summary) == 80
    assert result.summary.endswith("…")


def test_compactor_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_recent_observations"):
        ObservationCompactor(max_recent_observations=0)

    with pytest.raises(ValueError, match="max_summary_characters"):
        ObservationCompactor(max_summary_characters=0)
