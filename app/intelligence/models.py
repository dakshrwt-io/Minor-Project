"""Immutable data returned by Python repository structure analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportReference:
    """One import statement extracted from a Python source file."""

    module: str | None
    names: tuple[str, ...]
    relative_level: int
    line: int


@dataclass(frozen=True, slots=True)
class PythonSymbol:
    """One top-level Python declaration available within a module."""

    name: str
    kind: str
    line: int


@dataclass(frozen=True, slots=True)
class PythonFileInfo:
    """One parseable Python file, identified relative to the target root."""

    path: Path
    module_name: str
    is_package: bool
    imports: tuple[ImportReference, ...]
    symbols: tuple[PythonSymbol, ...]


@dataclass(frozen=True, slots=True)
class ModuleRelationship:
    """An import edge resolved to another indexed module in the target root."""

    source_module: str
    target_module: str
    line: int


@dataclass(frozen=True, slots=True)
class ParseIssue:
    """A file that could not be indexed, without failing the project scan."""

    path: Path
    message: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class PythonProjectIndex:
    """Read-only Python file structure, imports, and recoverable parse issues."""

    target_root: Path
    files: tuple[PythonFileInfo, ...]
    relationships: tuple[ModuleRelationship, ...]
    issues: tuple[ParseIssue, ...]
