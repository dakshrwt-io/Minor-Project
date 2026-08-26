"""SQLite persistence boundary for coding-agent session metadata."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from app.memory.models import SessionRecord


class SessionStore(ABC):
    """Storage contract for session metadata and compacted summaries."""

    @abstractmethod
    def create(self, session: SessionRecord) -> None:
        """Persist a newly created session."""

    @abstractmethod
    def get(self, session_id: str) -> SessionRecord | None:
        """Load a session by its stable identifier."""

    @abstractmethod
    def update_summary(self, session_id: str, summary: str) -> SessionRecord | None:
        """Persist a replacement summary and return the updated record."""


class SqliteSessionStore(SessionStore):
    """Store session metadata in a service-owned SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path.resolve()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def create(self, session: SessionRecord) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id, target_root, task, summary, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        str(session.target_root),
                        session.task,
                        session.summary,
                        session.created_at.isoformat(),
                        session.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"session already exists: {session.session_id}") from exc

    def get(self, session_id: str) -> SessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, target_root, task, summary, created_at, updated_at
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def update_summary(self, session_id: str, summary: str) -> SessionRecord | None:
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET summary = ?, updated_at = ? WHERE session_id = ?",
                (summary, updated_at, session_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get(session_id)

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    target_root TEXT NOT NULL,
                    task TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)

    @staticmethod
    def _row_to_session(row: tuple[str, str, str, str, str, str]) -> SessionRecord:
        return SessionRecord(
            session_id=row[0],
            target_root=Path(row[1]),
            task=row[2],
            summary=row[3],
            created_at=datetime.fromisoformat(row[4]),
            updated_at=datetime.fromisoformat(row[5]),
        )
