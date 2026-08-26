"""AST-based extraction of Python project files and import relationships."""

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
        if reference.relative_level:
            package_parts = file.module_name.split(".")
            if not file.is_package:
                package_parts = package_parts[:-1]
            parent_count = reference.relative_level - 1
            if parent_count > len(package_parts):
                return ()
            base_parts = package_parts[: len(package_parts) - parent_count]
            if reference.module:
                base_parts.extend(reference.module.split("."))
            base_module = ".".join(base_parts)
        else:
            base_module = reference.module or ""

        targets = {base_module} if base_module and reference.module else set()
        if reference.names:
            targets.update(
                f"{base_module}.{name}" if base_module else name for name in reference.names
            )
        return tuple(sorted(target for target in targets if target))
