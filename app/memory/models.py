"""Persistent session data owned by the coding-agent service."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _new_session_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SessionRecord(BaseModel):
    """A persisted coding-agent session, independent of the active graph state."""

    model_config = ConfigDict(frozen=True)

    session_id: str = Field(default_factory=_new_session_id, min_length=1)
    target_root: Path
    task: str = Field(min_length=1)
    summary: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        return value.astimezone(UTC)
