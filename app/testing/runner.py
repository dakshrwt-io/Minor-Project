"""Run an explicitly configured target-repository test command."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CONFIG_FILE_NAME = ".coding-agent.toml"
_DEFAULT_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TestCommand:
    """A shell-free test command supplied by a target repository."""

    __test__ = False

    arguments: tuple[str, ...]
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class TestRunResult:
    """An auditable outcome from one target-repository test command."""

    command: TestCommand
    passed: bool
    output: str
    return_code: int | None
    timed_out: bool = False
    error: str | None = None


class TestRunner:
    """Discover and execute a test command without invoking a shell."""

    __test__ = False

    def __init__(self, target_root: Path, command: TestCommand) -> None:
        resolved_root = target_root.resolve()
        if not resolved_root.is_dir():
            raise ValueError("target_root must be an existing directory")
        self._target_root = resolved_root
        self._command = command

    @staticmethod
    def discover(target_root: Path) -> TestCommand | None:
        """Load optional test configuration from ``.coding-agent.toml``.

        A repository must opt in with a ``[test]`` section containing a
        non-empty ``command`` argument list. The agent never turns model text
        into a shell command.
        """

        config_path = target_root.resolve() / _CONFIG_FILE_NAME
        if not config_path.is_file():
            return None
        try:
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid {_CONFIG_FILE_NAME}: {exc}") from exc

        section = payload.get("test")
        if not isinstance(section, dict):
            raise ValueError(f"{_CONFIG_FILE_NAME} must contain a [test] section")
        return TestRunner._parse_command(section)

    def run(self) -> TestRunResult:
        """Run the configured command within the target root and capture output."""

        try:
            completed = subprocess.run(
                self._command.arguments,
                cwd=self._target_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._command.timeout_seconds,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            return TestRunResult(
                command=self._command,
                passed=False,
                output="",
                return_code=None,
                error=f"test command was not found: {exc.filename}",
            )
        except subprocess.TimeoutExpired as exc:
            return TestRunResult(
                command=self._command,
                passed=False,
                output=self._combine_output(exc.stdout, exc.stderr),
                return_code=None,
                timed_out=True,
                error=f"test command exceeded {self._command.timeout_seconds} seconds",
            )

        output = self._combine_output(completed.stdout, completed.stderr)
        return TestRunResult(
            command=self._command,
            passed=completed.returncode == 0,
            output=output,
            return_code=completed.returncode,
            error=None if completed.returncode == 0 else "test command exited with a non-zero status",
        )

    @staticmethod
    def _parse_command(section: dict[str, Any]) -> TestCommand:
        arguments = section.get("command")
        if not isinstance(arguments, list) or not arguments or not all(
            isinstance(argument, str) and argument for argument in arguments
        ):
            raise ValueError("test.command must be a non-empty list of strings")

        timeout_seconds = section.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValueError("test.timeout_seconds must be a positive integer")
        return TestCommand(arguments=tuple(arguments), timeout_seconds=timeout_seconds)

    @staticmethod
    def _combine_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
        def as_text(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""

        return f"{as_text(stdout)}{as_text(stderr)}"
