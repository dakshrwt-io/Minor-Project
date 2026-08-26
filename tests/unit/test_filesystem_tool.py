from pathlib import Path

from app.contracts import FilesystemOperation, ToolCall
from app.tools.filesystem import FilesystemTool


def make_call(
    operation: FilesystemOperation, path: str, arguments: dict[str, str] | None = None
) -> ToolCall:
    return ToolCall(
        tool_name="filesystem",
        operation=operation,
        path=Path(path),
        arguments=arguments or {},
    )


def test_list_and_read_stay_within_target_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    tool = FilesystemTool(tmp_path)

    listing = tool.execute(make_call(FilesystemOperation.LIST, "src"))
    reading = tool.execute(make_call(FilesystemOperation.READ, "src/main.py"))

    assert listing.succeeded
    assert listing.output == "src/main.py"
    assert reading.succeeded
    assert reading.output == "print('hello')\n"


def test_create_write_and_edit_file(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path, allow_changes=True)

    created = tool.execute(
        make_call(FilesystemOperation.CREATE, "notes.txt", {"content": "draft"})
    )
    written = tool.execute(make_call(FilesystemOperation.WRITE, "notes.txt", {"content": "hello world"}))
    edited = tool.execute(
        make_call(
            FilesystemOperation.EDIT,
            "notes.txt",
            {"old_text": "world", "new_text": "agent"},
        )
    )

    assert created.succeeded
    assert written.succeeded
    assert edited.succeeded
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello agent"


def test_change_operations_require_explicit_authorization(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    result = tool.execute(
        make_call(FilesystemOperation.CREATE, "notes.txt", {"content": "draft"})
    )

    assert not result.succeeded
    assert result.error == (
        "permission denied: create operation requires apply_changes=True on the agent request"
    )
    assert not (tmp_path / "notes.txt").exists()


def test_rejects_path_outside_target_root(tmp_path: Path) -> None:
    tool = FilesystemTool(tmp_path)

    result = tool.execute(make_call(FilesystemOperation.READ, "../secret.txt"))

    assert not result.succeeded
    assert result.error == "requested path escapes the target repository"


def test_edit_requires_exactly_one_match(tmp_path: Path) -> None:
    file_path = tmp_path / "repeated.txt"
    file_path.write_text("same same", encoding="utf-8")
    tool = FilesystemTool(tmp_path, allow_changes=True)

    result = tool.execute(
        make_call(
            FilesystemOperation.EDIT,
            "repeated.txt",
            {"old_text": "same", "new_text": "changed"},
        )
    )

    assert not result.succeeded
    assert result.error == "edit operation requires old_text to occur exactly once"
    assert file_path.read_text(encoding="utf-8") == "same same"
