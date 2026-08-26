"""Filesystem permission decisions for the Phase 2 execution boundary."""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import FilesystemOperation, ToolCall


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """Whether one tool call may proceed, with an auditable explanation."""

    allowed: bool
    reason: str


class FilesystemPermissionPolicy:
    """Allow inspection by default and require explicit edit authorization."""

    _read_only_operations = frozenset({FilesystemOperation.LIST, FilesystemOperation.READ})

    def __init__(self, *, allow_changes: bool) -> None:
        self._allow_changes = allow_changes

    def evaluate(self, call: ToolCall) -> PermissionDecision:
        """Return the policy decision before the filesystem operation runs."""

        if call.operation in self._read_only_operations:
            return PermissionDecision(allowed=True, reason="read-only operation allowed")
        if self._allow_changes:
            return PermissionDecision(allowed=True, reason="change operation explicitly authorized")
        return PermissionDecision(
            allowed=False,
            reason=(
                f"{call.operation.value} operation requires apply_changes=True "
                "on the agent request"
            ),
        )
