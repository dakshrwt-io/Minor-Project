from datetime import datetime
from pathlib import Path

import pytest

from app.memory.models import SessionRecord
from app.memory.store import SqliteSessionStore


def test_session_store_persists_and_loads_a_session(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent-state.sqlite3")
    session = SessionRecord(target_root=tmp_path / "target", task="Review the README")

    store.create(session)

    loaded = store.get(session.session_id)
    assert loaded == session


def test_session_store_updates_a_summary(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent-state.sqlite3")
    session = SessionRecord(target_root=tmp_path / "target", task="Fix a test")
    store.create(session)

    updated = store.update_summary(session.session_id, "A test was fixed and passed.")

    assert updated is not None
    assert updated.summary == "A test was fixed and passed."
    assert updated.updated_at >= session.updated_at


def test_session_store_returns_none_for_unknown_session(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent-state.sqlite3")

    assert store.get("missing") is None
    assert store.update_summary("missing", "No session") is None


def test_session_store_rejects_duplicate_session_ids(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path / "agent-state.sqlite3")
    session = SessionRecord(session_id="session-1", target_root=tmp_path, task="Inspect project")
    store.create(session)

    with pytest.raises(ValueError, match="session already exists"):
        store.create(session)


def test_session_record_requires_timezone_aware_timestamps(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SessionRecord(
            target_root=tmp_path,
            task="Inspect project",
            created_at=datetime(2026, 1, 1),
        )
