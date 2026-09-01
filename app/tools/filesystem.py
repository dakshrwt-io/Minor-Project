"""Target-root-confined filesystem operations for the agent."""

from __future__ import annotations

from pathlib import Path

from app.contracts import FilesystemOperation, ToolCall, ToolResult

_READ_ONLY_OPERATIONS = frozenset({FilesystemOperation.LIST, FilesystemOperation.READ})


class FilesystemTool:
    """Perform the five file operations exposed to the agent.

    list, read, create, write, and edit — nothing else. Deletion and shell
    execution deliberately do not exist here, so no permission check is even
    needed to reject them: the model cannot ask for what is not implemented.

    Every requested path is resolved against a fixed target repository root.
    Paths outside that root — including traversal through ``..`` and absolute
    paths pointing elsewhere — are rejected. Inspection is always allowed;
    create/write/edit require ``allow_changes=True``.

    Operations report failure as a ToolResult instead of raising: the ReAct
    loop must be able to observe a failed action and let the model retry, and
    one bad tool call must never crash the request.
    """

    name = "filesystem"

    def __init__(self, target_root: Path, *, allow_changes: bool = False) -> None:
        resolved_root = target_root.resolve()
        if not resolved_root.is_dir():
            raise ValueError("target_root must be an existing directory")
        self._target_root = resolved_root
        self._allow_changes = allow_changes

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute one call and return an auditable result instead of raising.

        Order of checks: identity, then permission, then confinement, then
        the operation itself — so the audit trail shows which gate rejected
        a call, if any.
        """

        if call.tool_name != self.name:
            return self._failure(call, f"tool '{call.tool_name}' is not handled by {self.name}")

        permission_error = self._check_permission(call)
        if permission_error is not None:
            return self._failure(call, permission_error)

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

    def _check_permission(self, call: ToolCall) -> str | None:
        """Return an error message when the operation is not authorized, else None."""

        if call.operation in _READ_ONLY_OPERATIONS or self._allow_changes:
            return None
        return (
            f"permission denied: {call.operation.value} operation requires "
            "apply_changes=True on the agent request"
        )

    def _resolve_path(self, requested_path: Path) -> Path:
        """Confine one model-supplied path to the target root, or raise.

        The target root is fixed at construction, but the path arrives fresh
        from the model on every call, so it is re-resolved and re-validated
        every time: ``resolve(strict=False)`` collapses ``..`` segments and
        follows symlinks to their real location, and the ``relative_to`` check
        then rejects anything that lands outside the root. Re-checking per
        call (instead of trusting earlier validations) also catches symlink
        escapes created by earlier agent writes during the same session.
        """

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
        path.write_text(self._string_argument(call, "content"), encoding="utf-8")
        return self._success(call, f"created {path.relative_to(self._target_root).as_posix()}")

    def _write(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_file():
            raise ValueError("write operation requires an existing file")
        path.write_text(self._string_argument(call, "content"), encoding="utf-8")
        return self._success(call, f"wrote {path.relative_to(self._target_root).as_posix()}")

    def _edit(self, call: ToolCall, path: Path) -> ToolResult:
        if not path.is_file():
            raise ValueError("edit operation requires an existing file")
        old_text = self._string_argument(call, "old_text")
        new_text = self._string_argument(call, "new_text")
        content = path.read_text(encoding="utf-8")
        # Exactly-one-occurrence rule: zero matches would make the edit a
        # silent no-op, several would make the replaced spot unpredictable —
        # either way the model must narrow the anchor and retry.
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError("edit operation requires old_text to occur exactly once")
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return self._success(call, f"edited {path.relative_to(self._target_root).as_posix()}")

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
