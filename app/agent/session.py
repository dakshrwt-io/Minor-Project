"""Bounded conversation memory over the SDK's SQLiteSession."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import SQLiteSession
from agents.memory.session import SessionABC

# Cross-request conversation window: older session items are hidden from the
# model so one long REPL session cannot grow the prompt without bound.
_MAX_SESSION_ITEMS = 40


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


class BoundedSession(SessionABC):
    """Conversation memory over the SDK's SQLiteSession with bounded replay.

    The old SessionStore clipped both sides of each stored turn (800 chars)
    so conversation history stays context, not a transcript replay; this
    Session subclass preserves that clipping, hides items beyond a fixed
    window from the model, and lets SQLiteSession own storage and eviction.
    """

    MAX_MESSAGE_CHARS = 800
    MAX_REPLY_CHARS = 800

    def __init__(self, session_id: str, db_path: str | Path) -> None:
        self.session_id = session_id
        self._delegate = SQLiteSession(session_id, db_path)

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = await self._delegate.get_items(limit)
        if limit is not None or len(items) <= _MAX_SESSION_ITEMS:
            return items
        windowed = items[-_MAX_SESSION_ITEMS:]
        # Never open the window with an orphaned tool output: chat-completions
        # providers reject a tool result whose call is not present.
        while windowed and windowed[0].get("type") == "function_call_output":
            windowed = windowed[1:]
        return windowed

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        clipped: list[dict[str, Any]] = []
        for item in items:
            role = item.get("role") if isinstance(item, dict) else None
            if role == "user" and isinstance(item.get("content"), str):
                item = {**item, "content": _clip(item["content"], self.MAX_MESSAGE_CHARS)}
            elif role == "assistant" and isinstance(item.get("content"), str):
                item = {**item, "content": _clip(item["content"], self.MAX_REPLY_CHARS)}
            clipped.append(item)
        await self._delegate.add_items(clipped)

    async def pop_item(self) -> dict[str, Any] | None:
        return await self._delegate.pop_item()

    async def clear_session(self) -> None:
        await self._delegate.clear_session()
