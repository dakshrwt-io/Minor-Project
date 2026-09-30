"""Tests for saving and loading chat memory."""

from app import config, memory


def use_temp_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DB", str(tmp_path / "test.sqlite3"))


def test_new_session_is_empty(tmp_path, monkeypatch):
    use_temp_database(tmp_path, monkeypatch)
    assert memory.load_messages("new-session") == []


def test_save_then_load(tmp_path, monkeypatch):
    use_temp_database(tmp_path, monkeypatch)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "my name is Daksh"},
        {"role": "assistant", "content": "Nice to meet you"},
    ]

    memory.save_messages("s1", messages)

    assert memory.load_messages("s1") == messages


def test_sessions_are_separate(tmp_path, monkeypatch):
    use_temp_database(tmp_path, monkeypatch)
    memory.save_messages("s1", [{"role": "user", "content": "hello"}])

    assert memory.load_messages("s2") == []


def test_trim_keeps_last_turns_and_system_prompt(monkeypatch):
    monkeypatch.setattr(memory, "MAX_TURNS", 2)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "two"},
        {"role": "tool", "content": "result"},
        {"role": "user", "content": "three"},
    ]

    trimmed = memory.trim(messages)

    assert [message["content"] for message in trimmed] == ["rules", "two", "result", "three"]
