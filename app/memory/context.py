"""Deterministic, bounded compaction for active ReAct observations."""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import TestResult, ToolResult


@dataclass(frozen=True, slots=True)
class CompactedContext:
    """A short summary of older observations plus recent full observations."""

    summary: str
    recent_observations: list[ToolResult | TestResult]


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

    def compact(self, observations: list[ToolResult | TestResult]) -> CompactedContext:
        """Return bounded prior context without dropping recent execution detail."""

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
    def _describe(observation: ToolResult | TestResult) -> str:
        if isinstance(observation, ToolResult):
            outcome = "succeeded" if observation.succeeded else f"failed: {observation.error}"
            detail = observation.output or observation.error or "no output"
            return (
                f"filesystem {observation.call.operation.value} {observation.call.path}: "
                f"{outcome}; {ObservationCompactor._excerpt(detail)}"
            )

        outcome = "passed" if observation.passed else f"failed: {observation.error}"
        detail = observation.output or observation.error or "no output"
        return f"test command: {outcome}; {ObservationCompactor._excerpt(detail)}"

    @staticmethod
    def _excerpt(value: str, limit: int = 240) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"
