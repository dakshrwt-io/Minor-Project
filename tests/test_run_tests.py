"""Tests for the run_tests tool. They run a real pytest inside a temporary project."""

import sys

from app import config, tools
from app.tools import run_tool


def make_project(tmp_path, monkeypatch, test_code):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # a project of its own
    (tmp_path / "test_math.py").write_text(test_code, encoding="utf-8")
    # sys.executable = the Python running these tests, so pytest is surely installed.
    monkeypatch.setattr(config, "TEST_COMMAND", [sys.executable, "-m", "pytest", "-q"])
    return tmp_path


def test_passing_tests(tmp_path, monkeypatch):
    repo = make_project(tmp_path, monkeypatch, "def test_add():\n    assert 1 + 1 == 2\n")

    result = run_tool(repo, "run_tests", {}, True)

    assert result.startswith("Tests PASSED")
    assert "1 passed" in result


def test_failing_tests(tmp_path, monkeypatch):
    repo = make_project(tmp_path, monkeypatch, "def test_add():\n    assert 1 + 1 == 3\n")

    result = run_tool(repo, "run_tests", {}, True)

    assert result.startswith("Tests FAILED (exit code 1)")
    assert "1 failed" in result


def test_needs_apply_changes(tmp_path, monkeypatch):
    repo = make_project(tmp_path, monkeypatch, "def test_add():\n    pass\n")

    assert run_tool(repo, "run_tests", {}, False) == "Error: tool 'run_tests' is not available"


def test_slow_tests_are_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TEST_TIMEOUT_SECONDS", 1)
    never_ending = [sys.executable, "-c", "import time; time.sleep(10)"]
    monkeypatch.setattr(config, "TEST_COMMAND", never_ending)

    result = run_tool(tmp_path, "run_tests", {}, True)

    assert result == "Error: tests took longer than 1 seconds and were stopped"
