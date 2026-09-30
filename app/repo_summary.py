"""Repository summary: a short map of the Python code, made with Python's `ast` module.

AST = "abstract syntax tree". ast.parse() reads Python source code and turns
it into a tree of objects (functions, classes, imports) WITHOUT running the
code. We look at the top level of that tree and write down the names.

The summary goes into the system prompt, so the model already knows which
file holds what before it reads anything. That saves tool calls (and steps).
"""

import ast
from pathlib import Path

SKIP_FOLDERS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".test-tmp"}
MAX_FILES = 30  # files listed in the map
MAX_NAMES = 30  # names listed per kind (functions, classes, imports) per file
MAX_CHARS = 3000  # total size of the map


def find_python_files(repo: Path) -> list[Path]:
    """All .py files in the repo, skipping folders like .git and .venv."""
    files = []
    for path in sorted(repo.rglob("*.py")):
        relative = path.relative_to(repo)
        if any(part in SKIP_FOLDERS for part in relative.parts):
            continue
        files.append(path)
    return files


def describe_file(path: Path) -> str:
    """One line listing the classes, functions and imports defined in one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as error:
        return f"could not read ({type(error).__name__})"

    classes = []
    functions = []
    imports = []
    for node in tree.body:  # tree.body = the top-level statements of the file
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.Import):  # import os, json
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:  # from pathlib import Path
            imports.append(node.module)

    parts = []
    if classes:
        parts.append("classes: " + ", ".join(classes[:MAX_NAMES]))
    if functions:
        parts.append("functions: " + ", ".join(functions[:MAX_NAMES]))
    if imports:
        parts.append("imports: " + ", ".join(imports[:MAX_NAMES]))
    return "; ".join(parts) or "no classes or functions"


def summarize_repo(repo: Path) -> str:
    """The full map as text, cut to MAX_CHARS so it never fills the prompt."""
    repo = repo.resolve()
    files = find_python_files(repo)
    if not files:
        return "Repository map: no Python files found."

    lines = [f"Repository map ({len(files)} Python files):"]
    for path in files[:MAX_FILES]:
        relative = path.relative_to(repo).as_posix()
        lines.append(f"- {relative}: {describe_file(path)}")
    if len(files) > MAX_FILES:
        lines.append(f"- ... and {len(files) - MAX_FILES} more files")

    summary = "\n".join(lines)
    if len(summary) > MAX_CHARS:
        summary = summary[:MAX_CHARS] + "\n... (map cut off)"
    return summary
