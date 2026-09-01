"""AST-based extraction of Python project files and import relationships.

Single responsibility: turn a target repository into a PythonProjectIndex —
every Python file with its module name, top-level symbols, and imports —
plus the module-to-module relationship graph implied by those imports.

In the request lifecycle this runs inside the orchestrator (per request, in a
worker thread) before the ReAct loop starts: the index feeds the project
summarizer, whose summary is embedded in the prompt the model sees, so the
agent reasons about repo structure rather than guessing from file names.

Non-obvious choice: only stdlib `ast` + `pathlib` are used — no tree-sitter
or external indexer. Parse errors are captured as ParseIssue records instead
of failing the whole analysis, so one broken file never blocks a request.
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path

from app.intelligence.models import (
    ImportReference,
    ModuleRelationship,
    ParseIssue,
    PythonFileInfo,
    PythonProjectIndex,
    PythonSymbol,
)


class PythonProjectAnalyzer:
    """Build a small, read-only Python structure index for one target root."""

    _ignored_directories = frozenset({
        ".git",
        ".pytest_cache",
        ".test-tmp",
        ".venv",
        "__pycache__",
    })

    def analyze(self, target_root: Path) -> PythonProjectIndex:
        """Extract Python files and imports, retaining recoverable parse errors."""

        resolved_root = target_root.resolve()
        if not resolved_root.is_dir():
            raise ValueError("target_root must be an existing directory")

        files: list[PythonFileInfo] = []
        issues: list[ParseIssue] = []
        for source_path in self._python_files(resolved_root):
            relative_path = source_path.relative_to(resolved_root)
            try:
                files.append(self._analyze_file(source_path, relative_path))
            except (OSError, SyntaxError, UnicodeError) as exc:
                issues.append(
                    ParseIssue(
                        path=relative_path,
                        message=str(exc),
                        line=exc.lineno if isinstance(exc, SyntaxError) else None,
                    )
                )

        return PythonProjectIndex(
            target_root=resolved_root,
            files=tuple(files),
            relationships=self._module_relationships(files),
            issues=tuple(issues),
        )

    def _python_files(self, target_root: Path) -> list[Path]:
        candidates = [
            path
            for path in target_root.rglob("*.py")
            if not any(part in self._ignored_directories for part in path.relative_to(target_root).parts)
            and path.is_file()
        ]
        return sorted(candidates, key=lambda path: path.relative_to(target_root).as_posix())

    @staticmethod
    def _analyze_file(source_path: Path, relative_path: Path) -> PythonFileInfo:
        with tokenize.open(source_path) as source_file:
            tree = ast.parse(source_file.read(), filename=relative_path.as_posix())

        imports: list[ImportReference] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    ImportReference(
                        module=alias.name,
                        names=(),
                        relative_level=0,
                        line=node.lineno,
                    )
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    ImportReference(
                        module=node.module,
                        names=tuple(alias.name for alias in node.names),
                        relative_level=node.level,
                        line=node.lineno,
                    )
                )
        return PythonFileInfo(
            path=relative_path,
            module_name=PythonProjectAnalyzer._module_name(relative_path),
            is_package=relative_path.name == "__init__.py",
            imports=tuple(sorted(imports, key=lambda reference: reference.line)),
            symbols=PythonProjectAnalyzer._top_level_symbols(tree),
        )

    @staticmethod
    def _module_name(relative_path: Path) -> str:
        parts = relative_path.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    @staticmethod
    def _top_level_symbols(tree: ast.Module) -> tuple[PythonSymbol, ...]:
        symbols: list[PythonSymbol] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                symbols.append(PythonSymbol(name=node.name, kind=kind, line=node.lineno))
            elif isinstance(node, ast.ClassDef):
                symbols.append(PythonSymbol(name=node.name, kind="class", line=node.lineno))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                symbols.extend(PythonProjectAnalyzer._assignment_symbols(node))
        return tuple(sorted(symbols, key=lambda symbol: symbol.line))

    @staticmethod
    def _assignment_symbols(node: ast.Assign | ast.AnnAssign) -> list[PythonSymbol]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        symbols: list[PythonSymbol] = []
        for target in targets:
            for name in PythonProjectAnalyzer._target_names(target):
                symbols.append(PythonSymbol(name=name, kind="variable", line=node.lineno))
        return symbols

    @staticmethod
    def _target_names(target: ast.expr) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in PythonProjectAnalyzer._target_names(element)]
        return []

    @staticmethod
    def _module_relationships(files: list[PythonFileInfo]) -> tuple[ModuleRelationship, ...]:
        known_modules = {file.module_name for file in files}
        relationships = {
            ModuleRelationship(source_module=file.module_name, target_module=target, line=reference.line)
            for file in files
            for reference in file.imports
            for target in PythonProjectAnalyzer._import_targets(file, reference)
            if target in known_modules
        }
        return tuple(
            sorted(
                relationships,
                key=lambda relationship: (
                    relationship.source_module,
                    relationship.line,
                    relationship.target_module,
                ),
            )
        )

    @staticmethod
    def _import_targets(file: PythonFileInfo, reference: ImportReference) -> tuple[str, ...]:
        """Resolve one import statement to fully-qualified candidate module names.

        Worked example — `from ..models import Foo` inside app/orchestrator/graph.py:

            file.module_name   = "app.orchestrator.graph"   (not a package __init__)
            package_parts      = ["app", "orchestrator"]    (module path minus its
                                                          own name, since graph.py is
                                                          a module, not a package)
            relative_level     = 2   (two dots: one dot = current package,
                                      each extra dot = one package up)
            parent_count       = 2 - 1 = 1   (level 1 stays in the current package;
                                              every level beyond 1 climbs one parent)
            base_parts         = ["app"]     (drop `parent_count` entries from the
                                              right end of package_parts)
            + reference.module = ["app", "models"]       ("models" appended)
            base_module        = "app.models"

        Candidates returned: {"app.models", "app.models.Foo"} — the package itself
        (importing the package runs its __init__.py) and each imported name. Only
        candidates that match a module actually present in the repo survive the
        filter in _module_relationships, so stdlib/third-party imports drop out.

        Absolute imports (relative_level == 0) skip the walk-up entirely:
        `from fastapi import APIRouter` → {"fastapi", "fastapi.APIRouter"}.
        """

        if reference.relative_level:
            # Start from the importing module's own dotted path, then step up
            # to its containing package. For __init__.py the module name IS
            # the package, so nothing is dropped; for a plain module the last
            # part (its own name) is removed.
            package_parts = file.module_name.split(".")
            if not file.is_package:
                package_parts = package_parts[:-1]

            # One dot = the containing package; each additional dot = one
            # more package above it, hence (level - 1) parents to strip.
            parent_count = reference.relative_level - 1
            if parent_count > len(package_parts):
                # More dots than available packages (e.g. ... from a top-level
                # module) — the import cannot resolve inside this repo.
                return ()
            base_parts = package_parts[: len(package_parts) - parent_count]

            # `from ..pkg import y` names a subpackage/attribute below the
            # anchor computed above; append it to the anchor.
            if reference.module:
                base_parts.extend(reference.module.split("."))
            base_module = ".".join(base_parts)
        else:
            base_module = reference.module or ""

        # Candidates: the imported module itself, plus each `from X import a, b`
        # name as a child of it. An empty base (e.g. bare `import` with no
        # module) contributes nothing on its own.
        targets = {base_module} if base_module and reference.module else set()
        if reference.names:
            targets.update(
                f"{base_module}.{name}" if base_module else name for name in reference.names
            )
        return tuple(sorted(target for target in targets if target))
