import sys
from pathlib import Path

import pytest

from app.testing.runner import TestCommand, TestRunner


def test_discover_loads_explicit_target_repository_command(tmp_path: Path) -> None:
    (tmp_path / ".coding-agent.toml").write_text(
        "[test]\ncommand = [\"python\", \"-m\", \"pytest\"]\ntimeout_seconds = 30\n",
        encoding="utf-8",
    )

    command = TestRunner.discover(tmp_path)

    assert command == TestCommand(arguments=("python", "-m", "pytest"), timeout_seconds=30)


def test_discover_returns_none_when_target_has_no_configuration(tmp_path: Path) -> None:
    assert TestRunner.discover(tmp_path) is None


def test_discover_rejects_an_invalid_command(tmp_path: Path) -> None:
    (tmp_path / ".coding-agent.toml").write_text("[test]\ncommand = \"pytest\"\n", encoding="utf-8")

    with pytest.raises(ValueError, match="test.command"):
        TestRunner.discover(tmp_path)


def test_runner_captures_a_passing_command(tmp_path: Path) -> None:
    command = TestCommand(arguments=(sys.executable, "-c", "print('tests passed')"))

    result = TestRunner(tmp_path, command).run()

    assert result.passed
    assert result.return_code == 0
    assert result.output == "tests passed\n"
    assert result.error is None


def test_runner_reports_a_nonzero_exit_status(tmp_path: Path) -> None:
    command = TestCommand(arguments=(sys.executable, "-c", "import sys; sys.exit(3)"))

    result = TestRunner(tmp_path, command).run()

    assert not result.passed
    assert result.return_code == 3
    assert result.error == "test command exited with a non-zero status"


def test_runner_reports_a_timeout(tmp_path: Path) -> None:
    command = TestCommand(
        arguments=(sys.executable, "-c", "import time; time.sleep(1)"), timeout_seconds=1
    )

    result = TestRunner(tmp_path, command).run()

    assert not result.passed
    assert result.timed_out
    assert result.error == "test command exceeded 1 seconds"
