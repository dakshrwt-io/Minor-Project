"""Volatile conversation memory across requests that share one session id.

Single responsibility: remember prior user messages and agent replies so a
multi-message conversation can reference earlier turns ("my name is Daksh"
followed by "what is my name"), without persisting anything.

Design:
- Session identity is client-owned. The interactive REPL mints one UUID at
  startup and sends it with every request; a request without a session id
  gets a freshly minted one, so single-shot requests stay
  one-message-per-session and an agent restart is always a new session.
- The store lives in RAM inside the gateway process and dies with it — the
  same volatility rationale as the rest of the memory layer.
- Bounded like everything else: per-session turn count, per-side text caps
  applied at record time, and a global cap on remembered sessions with
  oldest-first eviction. No model calls, no embeddings: the same prompt for
  the same turns, every time, which keeps prompts reproducible.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

# Per-side text caps applied when a turn is recorded: conversation history is
# context for follow-ups, not a transcript replay, so long replies are clipped
# (the client still shows the user the full reply from its own response).
MAX_MESSAGE_CHARS = 800
MAX_REPLY_CHARS = 800


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One prior exchange: the user message, the agent's reply, and its route."""

    message: str
    reply: str
    route: str  # "chat" or "task"


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


class SessionStore:
    """Remember bounded conversation turns per session id, in RAM only."""

    def __init__(self, *, max_turns: int = 12, max_sessions: int = 512) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        self._turns: OrderedDict[str, list[ConversationTurn]] = OrderedDict()
        self._lock = Lock()

    def history(self, session_id: str) -> tuple[ConversationTurn, ...]:
        """Return every remembered turn for the session (oldest first)."""

        with self._lock:
            return tuple(self._turns.get(session_id, ()))

    def record(self, session_id: str, turn: ConversationTurn) -> None:
        """Append one turn, trimmed and bounded, refreshing session recency."""

        stored = ConversationTurn(
            message=_clip(turn.message, MAX_MESSAGE_CHARS),
            reply=_clip(turn.reply, MAX_REPLY_CHARS),
            route=turn.route,
        )
        with self._lock:
            turns = self._turns.pop(session_id, [])
            turns.append(stored)
            self._turns[session_id] = turns[-self._max_turns :]
            self._turns.move_to_end(session_id)
            while len(self._turns) > self._max_sessions:
                self._turns.popitem(last=False)

    def payload(self, session_id: str, *, max_turns: int = 6) -> list[dict[str, str]]:
        """Render the most recent turns as JSON-serializable prompt context.

        Called by the orchestrator once per request; the router, planner, and
        prompt builder serialize the result verbatim, so all three stages see
        byte-identical history.
        """

        turns = self.history(session_id)[-max_turns:]
        return [
            {"user": turn.message, "agent": turn.reply, "route": turn.route}
            for turn in turns
        ]
