"""Tests for the file tools and their safety rules."""

from app.tools import run_tool, tool_descriptions


def make_repo(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "README.md").write_text("Hello world", encoding="utf-8")
    (tmp_path / "src").mkdir()
    return tmp_path


def test_list_files(tmp_path):
    repo = make_repo(tmp_path)
    assert run_tool(repo, "list_files", {"path": "."}, False) == "README.md\nsrc/"


def test_read_file(tmp_path):
    repo = make_repo(tmp_path)
    assert run_tool(repo, "read_file", {"path": "README.md"}, False) == "Hello world"


def test_cannot_leave_the_repository(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (tmp_path / "secret.txt").write_text("password", encoding="utf-8")

    result = run_tool(repo, "read_file", {"path": "../secret.txt"}, False)

    assert result.startswith("Error:")
    assert "outside the repository" in result


def test_write_tools_need_apply_changes(tmp_path):
    repo = make_repo(tmp_path)

    result = run_tool(repo, "create_file", {"path": "new.txt", "content": "hi"}, False)

    assert result == "Error: tool 'create_file' is not available"
    assert not (repo / "new.txt").exists()


def test_model_only_sees_allowed_tools():
    read_only = ["list_files", "read_file", "search_files"]
    names = [tool["function"]["name"] for tool in tool_descriptions(False)]
    assert names == read_only

    names = [tool["function"]["name"] for tool in tool_descriptions(True)]
    assert names == read_only + ["create_file", "write_file", "edit_file"]


def test_create_write_and_edit(tmp_path):
    repo = make_repo(tmp_path)

    create = {"path": "a.txt", "content": "one"}
    write = {"path": "a.txt", "content": "two"}
    edit = {"path": "a.txt", "old_text": "two", "new_text": "three"}

    assert run_tool(repo, "create_file", create, True) == "Created a.txt"
    assert run_tool(repo, "write_file", write, True) == "Wrote a.txt"
    assert run_tool(repo, "edit_file", edit, True) == "Edited a.txt"

    assert (repo / "a.txt").read_text(encoding="utf-8") == "three"


def test_edit_needs_exactly_one_match(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "b.txt").write_text("x x", encoding="utf-8")

    result = run_tool(repo, "edit_file", {"path": "b.txt", "old_text": "x", "new_text": "y"}, True)

    assert result == "Error: old_text must appear exactly once, found 2 times"


def test_create_refuses_existing_file(tmp_path):
    repo = make_repo(tmp_path)

    result = run_tool(repo, "create_file", {"path": "README.md", "content": "bye"}, True)

    assert result.startswith("Error:")
    assert (repo / "README.md").read_text(encoding="utf-8") == "Hello world"


def test_secret_files_cannot_be_read(tmp_path):
    repo = make_repo(tmp_path)
    for name in [".env", ".env.local", "server.pem", "api.key"]:
        (repo / name).write_text("API_KEY=sk-12345", encoding="utf-8")

        result = run_tool(repo, "read_file", {"path": name}, False)

        assert result == f"Error: '{name}' may contain secrets, so it is blocked"


def test_secret_files_cannot_be_created(tmp_path):
    repo = make_repo(tmp_path)

    result = run_tool(repo, "create_file", {"path": ".env", "content": "x"}, True)

    assert result.startswith("Error:")
    assert not (repo / ".env").exists()


def test_normal_files_are_not_secret(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "keyboard.py").write_text("print('hi')", encoding="utf-8")

    assert run_tool(repo, "read_file", {"path": "keyboard.py"}, False) == "print('hi')"


def test_search_finds_file_and_line(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "shop.py").write_text("x = 1\ndef Add_Item():\n    pass\n", encoding="utf-8")

    result = run_tool(repo, "search_files", {"text": "add_item"}, False)

    assert result == "src/shop.py:2: def Add_Item():"


def test_search_skips_secret_files(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".env").write_text("API_KEY=sk-12345", encoding="utf-8")

    result = run_tool(repo, "search_files", {"text": "API_KEY"}, False)

    assert result == "No matches for 'API_KEY'"


def test_search_stops_at_result_limit(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "big.txt").write_text("match\n" * 500, encoding="utf-8")

    result = run_tool(repo, "search_files", {"text": "match"}, False)

    assert len(result.splitlines()) == 51  # 50 results + the "too many matches" line
