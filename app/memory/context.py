"""Deterministic, bounded compaction for active ReAct observations.

Single responsibility: keep the prompt's observation context within bounds
as the ReAct loop accumulates results. Called by the prompt builder on every
`act` iteration.

Design: recent observations stay verbatim (the model needs exact tool output
to decide its next action), while older observations collapse into a
one-line-each summary with a hard character cap. Compaction is deterministic
— no model call, no embeddings — so the same observation list always yields
the same summary, which keeps prompts reproducible and auditable.

Note: the per-observation line format here mirrors client/formatting.py's
describe_observation() (same excerpt limit and detail priority) but is
deliberately separate: this module renders model-facing summaries from typed
domain objects inside the server, while client/formatting.py renders
human-facing transcript lines from response dicts in the client. Sharing one
implementation would couple the app and client layers for ~15 lines.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import ExternalToolResult, TestResult, ToolResult


@dataclass(frozen=True, slots=True)
class CompactedContext:
    """A short summary of older observations plus recent full observations."""

    summary: str
    recent_observations: list[ToolResult | TestResult | ExternalToolResult]


class ObservationCompactor:
    """Keep recent observations intact while bounding older prompt context."""

    def __init__(
        self, *, max_recent_observations: int = 4, max_summary_characters: int = 1_500
    ) -> None:
        if max_recent_observations < 1:
            raise ValueError("max_recent_observations must be at least 1")
        if max_summary_characters < 1:
            raise ValueError("max_summary_characters must be at least 1")
        self._max_recent_observations = max_recent_observations
        self._max_summary_characters = max_summary_characters

    def compact(
        self, observations: list[ToolResult | TestResult | ExternalToolResult]
    ) -> CompactedContext:
        """Return bounded prior context without dropping recent execution detail.

        When everything fits the recent window, the summary is empty — no
        compaction happened, so the prompt carries none.
        """

        if len(observations) <= self._max_recent_observations:
            return CompactedContext(summary="", recent_observations=list(observations))

        older = observations[: -self._max_recent_observations]
        lines = [self._describe(observation) for observation in older]
        summary = f"Compacted {len(older)} earlier observation(s): " + " | ".join(lines)
        if len(summary) > self._max_summary_characters:
            summary = summary[: self._max_summary_characters - 1].rstrip() + "…"
        return CompactedContext(
            summary=summary,
            recent_observations=list(observations[-self._max_recent_observations :]),
        )

    @staticmethod
    def _describe(observation: ToolResult | TestResult | ExternalToolResult) -> str:
        """Render one observation as a single summary line (model-facing)."""

        if isinstance(observation, ToolResult):
            outcome = "succeeded" if observation.succeeded else f"failed: {observation.error}"
            detail = observation.output or observation.error or "no output"
            return (
                f"filesystem {observation.call.operation.value} {observation.call.path}: "
                f"{outcome}; {ObservationCompactor._excerpt(detail)}"
            )
        if isinstance(observation, ExternalToolResult):
            outcome = "succeeded" if observation.succeeded else f"failed: {observation.error}"
            detail = " ".join(observation.content) or observation.error or "no output"
            return (
                f"external tool {observation.tool_name}: {outcome}; "
                f"{ObservationCompactor._excerpt(detail)}"
            )

        outcome = "passed" if observation.passed else f"failed: {observation.error}"
        detail = observation.output or observation.error or "no output"
        return f"test command: {outcome}; {ObservationCompactor._excerpt(detail)}"

    @staticmethod
    def _excerpt(value: str, limit: int = 240) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"
