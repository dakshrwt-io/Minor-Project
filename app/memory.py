"""Chat memory: save and load conversations in a SQLite database.

Each conversation has a session_id. We store its whole `messages` list as
JSON text in one row. When the next message arrives with the same
session_id, we load the list again, so the model sees the earlier chat.
"""

import json
import sqlite3
from pathlib import Path

from app import config

# Only the last few user messages (and everything after them) are kept,
# so a long chat does not grow bigger than the model can read.
MAX_TURNS = 10


def connect() -> sqlite3.Connection:
    """Open the database file, creating the file and table the first time."""
    Path(config.MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.MEMORY_DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "session_id TEXT PRIMARY KEY, "
        "messages TEXT NOT NULL)"
    )
    return db


def trim(messages: list) -> list:
    """Keep the system prompt + the last MAX_TURNS user messages and what came after them.

    We always cut right before a user message. Cutting in the middle could
    leave a tool result without the tool request it answers, and the API
    rejects that.
    """
    system_prompt = messages[:1]
    rest = messages[1:]
    user_positions = [i for i, message in enumerate(rest) if message["role"] == "user"]   #message me user assistant tool sb hai yaha user wala message ki position find krre hai ex -  [1,3,5] etc
    if len(user_positions) <= MAX_TURNS:  # if user messages are less than or equal to max turns then return all messages
        return messages
    start = user_positions[-MAX_TURNS]  #slicing krre hai # if user messages are more than max turns then we will return last max turns user messages and everything after them
    return system_prompt + rest[start:] # return system prompt + last max massages.


def load_messages(session_id: str) -> list:
    """Return the saved conversation, or an empty list for a new session."""
    db = connect()
    row = db.execute(
        "SELECT messages FROM conversations WHERE session_id = ?", (session_id,)
    ).fetchone()
    db.close()
    if row is None:
        return []
    return json.loads(row[0])


def save_messages(session_id: str, messages: list) -> None:
    """Save the conversation, replacing the old copy for this session."""
    db = connect()
    db.execute(
        "INSERT OR REPLACE INTO conversations (session_id, messages) VALUES (?, ?)",
        (session_id, json.dumps(trim(messages))), #save_messages("abc123", ["Hello", "How are you?", "Tell me a joke"])
    )
    db.commit()
    db.close()
