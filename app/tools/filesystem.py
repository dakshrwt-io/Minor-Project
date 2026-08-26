"""Target-root-confined filesystem operations for the Phase 1 agent."""

from __future__ import annotations

from pathlib import Path

from app.contracts import FilesystemOperation, ToolCall, ToolResult
from app.guardrails.policy import FilesystemPermissionPolicy


class FilesystemTool:
    """Perform the limited file operations exposed to the first MVP.

    Every requested path is resolved against a fixed target repository root.
    Paths outside that root, including traversal through ``..``, are rejected.
    """

    name = "filesystem"

    def __init__(self, target_root: Path, *, allow_changes: bool = False) -> None:
        resolved_root = target_root.resolve()
        if not resolved_root.is_dir():
            raise ValueError("target_root must be an existing directory")
        self._target_root = resolved_root
        self._permission_policy = FilesystemPermissionPolicy(allow_changes=allow_changes)

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute one call and return an auditable result instead of raising."""

        if call.tool_name != self.name:
            return self._failure(call, f"tool '{call.tool_name}' is not handled by {self.name}")

        decision = self._permission_policy.evaluate(call)
        if not decision.allowed:
            return self._failure(call, f"permission denied: {decision.reason}")

        try:
            path = self._resolve_path(call.path)
            match call.operation:
                case FilesystemOperation.LIST:
                    return self._list(call, path)
                case FilesystemOperation.READ:
                    return self._read(call, path)
                case FilesystemOperation.CREATE:
                    return self._create(call, path)
                case FilesystemOperation.WRITE:
                    return self._write(call, path)
                case FilesystemOperation.EDIT:
                    return self._edit(call, path)
        except (OSError, ValueError) as exc:
            return self._failure(call, str(exc))

        return self._failure(call, f"unsupported operation: {call.operation}")

    def _resolve_path(self, requested_path: Path) -> Path:
        candidate = requested_path if requested_path.is_absolute() else self._target_root / requested_path
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._target_root)
        except ValueError as exc:
            raise ValueError("requested path escapes the target repository") from exc
        return resolved

    def _list(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_dir():
            raise ValueError("list operation requires an existing directory")
        entries = sorted(
            entry.relative_to(self._target_root).as_posix() for entry in path.iterdir()
        )
        return self._success(call, "\n".join(entries))

    def _read(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_file():
            raise ValueError("read operation requires an existing file")
        return self._success(call, path.read_text(encoding="utf-8"))

    def _create(self, call: ToolCall, path: Path) -> ToolResult:
        if path.exists():
            raise ValueError("create operation requires a path that does not exist")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._content_argument(call), encoding="utf-8")
        return self._success(call, f"created {path.relative_to(self._target_root).as_posix()}")

    def _write(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_file():
            raise ValueError("write operation requires an existing file")
        path.write_text(self._content_argument(call), encoding="utf-8")
        return self._success(call, f"wrote {path.relative_to(self._target_root).as_posix()}")

    def _edit(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_file():
            raise ValueError("edit operation requires an existing file")
        old_text = self._string_argument(call, "old_text")
        new_text = self._string_argument(call, "new_text")
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError("edit operation requires old_text to occur exactly once")
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return self._success(call, f"edited {path.relative_to(self._target_root).as_posix()}")

    @staticmethod
    def _content_argument(call: ToolCall) -> str:
        return FilesystemTool._string_argument(call, "content")

    @staticmethod
    def _string_argument(call: ToolCall, name: str) -> str:
        value = call.arguments.get(name)
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value

    @staticmethod
    def _success(call: ToolCall, output: str) -> ToolResult:
        return ToolResult(call=call, succeeded=True, output=output)

    @staticmethod
    def _failure(call: ToolCall, error: str) -> ToolResult:
        return ToolResult(call=call, succeeded=False, error=error)
