"""File tools the agent can use inside the target repository.

The model never touches files itself. It can only *ask* us to run one of
these functions, and we decide whether that is allowed.

Safety rules:
1. Every path must stay inside the target repository (see safe_path).
2. Tools that change files only exist when the user passed apply_changes=True.
3. There is no delete tool and no shell tool, so the model cannot ask for them.
4. Files that usually hold secrets (.env, private keys) are blocked (see is_secret).
"""

from pathlib import Path

from app.repo_summary import SKIP_FOLDERS

MAX_READ_CHARS = 20_000  # don't send huge files to the model
MAX_SEARCH_RESULTS = 50  # don't send thousands of matching lines to the model

# Files that usually contain passwords or API keys. Anything the agent reads is
# sent to the model provider, so these must never be opened.
SECRET_FILES = {".env", "id_rsa", "id_ed25519"}
SECRET_ENDINGS = (".pem", ".key")


def is_secret(path: Path) -> bool:
    """True for .env, .env.local, .env.production, *.pem, *.key, and SSH keys."""
    name = path.name.lower()
    return name in SECRET_FILES or name.startswith(".env.") or name.endswith(SECRET_ENDINGS)


def safe_path(repo: Path, relative_path: str) -> Path:
    """Turn a path from the model into a full path, but only if it is safe to use.

    resolve() removes things like ".." and follows shortcuts (symlinks),
    so "../../Windows" or a sneaky shortcut is caught here.
    Every tool calls this first, so both checks protect every tool.
    """
    repo = repo.resolve()
    full_path = (repo / relative_path).resolve()
    if not full_path.is_relative_to(repo):
        raise ValueError(f"'{relative_path}' is outside the repository")
    if is_secret(full_path):
        raise ValueError(f"'{relative_path}' may contain secrets, so it is blocked")
    return full_path


# ---------- read-only tools (always available) ----------


def list_files(repo: Path, path: str) -> str:
    folder = safe_path(repo, path)  # we are checking if the path is safe and not altered via tricks
    if not folder.is_dir():
        raise ValueError(f"'{path}' is not a folder")
    names = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            names.append(entry.name + "/")
        else:
            names.append(entry.name)
    return "\n".join(names) or "(empty folder)"  # At last we are returning the names of the files in the text format for llm.


def read_file(repo: Path, path: str) -> str:
    file = safe_path(repo, path)
    if not file.is_file():
        raise ValueError(f"'{path}' is not a file")
    text = file.read_text(encoding="utf-8")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + "\n... (file cut off, too long)" #just for safety and cost efficiency.
    return text


def search_files(repo: Path, text: str) -> str:
    """Find every line that contains `text` in any file of the repo (like Ctrl+Shift+F).

    Returns lines like "app/shop.py:12: def add(a, b):" so the model knows
    exactly which file and line to read next.
    """
    if not text:
        raise ValueError("text must not be empty")
    repo = repo.resolve()
    results = []
    for file in sorted(repo.rglob("*")):
        relative = file.relative_to(repo)
        if not file.is_file() or any(part in SKIP_FOLDERS for part in relative.parts):
            continue
        try:
            safe_path(repo, str(relative))  # same guard as every tool: no secrets, no escaping
            lines = file.read_text(encoding="utf-8").splitlines()
        except (ValueError, OSError):
            continue  # secret file, shortcut leading outside, or not a text file (e.g. an image)

        for number, line in enumerate(lines, start=1):
            if text.lower() in line.lower():  # lower() on both sides = ignore capital letters
                results.append(f"{relative.as_posix()}:{number}: {line.strip()[:200]}")
                if len(results) == MAX_SEARCH_RESULTS:
                    results.append("... (too many matches, search for something more specific)")
                    return "\n".join(results)

    return "\n".join(results) or f"No matches for '{text}'"


# ---------- tools that change files (only with apply_changes) ----------


def create_file(repo: Path, path: str, content: str) -> str:
    file = safe_path(repo, path)
    if file.exists():
        raise ValueError(f"'{path}' already exists, use write_file or edit_file")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    return f"Created {path}"


def write_file(repo: Path, path: str, content: str) -> str:
    file = safe_path(repo, path)
    if not file.is_file():
        raise ValueError(f"'{path}' does not exist, use create_file")
    file.write_text(content, encoding="utf-8")
    return f"Wrote {path}"


def edit_file(repo: Path, path: str, old_text: str, new_text: str) -> str:
    file = safe_path(repo, path)
    if not file.is_file():
        raise ValueError(f"'{path}' is not a file")
    text = file.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must appear exactly once, found {count} times")
    file.write_text(text.replace(old_text, new_text), encoding="utf-8")
    return f"Edited {path}"


READ_TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "search_files": search_files,
}

WRITE_TOOLS = {
    "create_file": create_file,
    "write_file": write_file,
    "edit_file": edit_file,
}


def available_tools(apply_changes: bool) -> dict:
    """Return the tools the model is allowed to use for this request."""
    if apply_changes:
        return {**READ_TOOLS, **WRITE_TOOLS}
    return dict(READ_TOOLS)


# ---------- descriptions sent to the model ----------
# The model reads these to know which tools exist and what arguments they take.
# This is the standard "function calling" format used by OpenAI-style APIs.


def _describe(name: str, description: str, arguments: dict[str, str]) -> dict:
    """Build one tool description. Every argument is a required string."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    arg: {"type": "string", "description": text} for arg, text in arguments.items()
                },
                "required": list(arguments),
            },
        },
    }


TOOL_DESCRIPTIONS = {
    "list_files": _describe(
        "list_files",
        "List files and folders in a folder of the repository.",
        {"path": 'Folder path relative to the repository root, "." for the root.'},
    ),
    "read_file": _describe(
        "read_file",
        "Read one text file from the repository.",
        {"path": "File path relative to the repository root."},
    ),
    "search_files": _describe(
        "search_files",
        "Find lines containing some text in all files of the repository (ignores capitals). "
        "Use it to find where a function, class or word is used.",
        {"text": "The text to search for, e.g. a function name."},
    ),
    "create_file": _describe(
        "create_file",
        "Create a new text file. Fails if the file already exists.",
        {"path": "Path of the new file.", "content": "Full text of the file."},
    ),
    "write_file": _describe(
        "write_file",
        "Replace the whole content of an existing file.",
        {"path": "Path of the file.", "content": "New full text of the file."},
    ),
    "edit_file": _describe(
        "edit_file",
        "Replace one exact piece of text in a file. old_text must appear exactly once.",
        {
            "path": "Path of the file.",
            "old_text": "Exact text to find.",
            "new_text": "Text to put in its place.",
        },
    ),
}


def tool_descriptions(apply_changes: bool) -> list[dict]:
    """Descriptions of only the tools allowed for this request."""
    return [TOOL_DESCRIPTIONS[name] for name in available_tools(apply_changes)]  


def run_tool(repo: Path, name: str, arguments: dict, apply_changes: bool) -> str:
    """Run one tool the model asked for and return the result as text.

    Errors are returned as text too (starting with "Error:"), so the model
    can read what went wrong and try something else. The agent never crashes
    because of one bad tool call.
    """
    tools = available_tools(apply_changes)
    if name not in tools:
        return f"Error: tool '{name}' is not available"
    try:
        return tools[name](repo, **arguments)
    except Exception as error:
        return f"Error: {error}"
