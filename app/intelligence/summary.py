"""Bounded, deterministic summaries of indexed Python repositories."""

from __future__ import annotations

from app.intelligence.models import PythonProjectIndex


class PythonProjectSummarizer:
    """Render useful project structure without allowing prompt context to grow unbounded."""

    def __init__(
        self,
        *,
        max_characters: int = 2_000,
        max_modules: int = 20,
        max_symbols_per_module: int = 8,
        max_relationships: int = 20,
    ) -> None:
        if min(max_characters, max_modules, max_symbols_per_module, max_relationships) < 1:
            raise ValueError("repository summary limits must be at least 1")
        self._max_characters = max_characters
        self._max_modules = max_modules
        self._max_symbols_per_module = max_symbols_per_module
        self._max_relationships = max_relationships

    def summarize(self, index: PythonProjectIndex) -> str:
        """Create a bounded textual summary suitable for prompt context."""

        lines = [
            "Python repository index: "
            f"{len(index.files)} module(s), {len(index.relationships)} internal import edge(s), "
            f"{len(index.issues)} parse issue(s)."
        ]
        for file in index.files[: self._max_modules]:
            symbols = ", ".join(
                f"{symbol.kind} {symbol.name}" for symbol in file.symbols[: self._max_symbols_per_module]
            ) or "no top-level symbols"
            lines.append(f"- {file.module_name} ({file.path.as_posix()}): {symbols}")
        if len(index.files) > self._max_modules:
            lines.append(f"- {len(index.files) - self._max_modules} additional module(s) omitted")

        for relationship in index.relationships[: self._max_relationships]:
            lines.append(f"- import: {relationship.source_module} -> {relationship.target_module}")
        if len(index.relationships) > self._max_relationships:
            lines.append(
                f"- {len(index.relationships) - self._max_relationships} additional import edge(s) omitted"
            )
        if index.issues:
            lines.append(f"- unparsed files: {', '.join(issue.path.as_posix() for issue in index.issues[:3])}")

        summary = "\n".join(lines)
        if len(summary) > self._max_characters:
            return summary[: self._max_characters - 1].rstrip() + "…"
        return summary
