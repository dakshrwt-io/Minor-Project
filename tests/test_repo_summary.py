"""Tests for the AST repository map."""

from app.repo_summary import summarize_repo


def test_lists_classes_functions_and_imports(tmp_path):
    code = "import os\nfrom pathlib import Path\nclass Shop:\n    pass\ndef add(a, b):\n    pass\n"
    (tmp_path / "shop.py").write_text(code, encoding="utf-8")

    summary = summarize_repo(tmp_path)

    assert "- shop.py: classes: Shop; functions: add; imports: os, pathlib" in summary


def test_broken_file_does_not_crash(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n", encoding="utf-8")

    summary = summarize_repo(tmp_path)

    assert "- broken.py: could not read (SyntaxError)" in summary


def test_skips_virtual_environment(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "library.py").write_text("def hidden():\n    pass\n", encoding="utf-8")

    assert summarize_repo(tmp_path) == "Repository map: no Python files found."


def test_code_is_never_run(tmp_path):
    (tmp_path / "danger.py").write_text("open('ran.txt', 'w')\n", encoding="utf-8")

    summarize_repo(tmp_path)

    assert not (tmp_path / "ran.txt").exists()
