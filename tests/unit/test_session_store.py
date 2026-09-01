from app.memory.session import MAX_MESSAGE_CHARS, MAX_REPLY_CHARS, ConversationTurn, SessionStore


def _turn(message: str = "hi", reply: str = "hello", route: str = "chat") -> ConversationTurn:
    return ConversationTurn(message=message, reply=reply, route=route)


def test_history_of_an_unknown_session_is_empty() -> None:
    store = SessionStore()

    assert store.history("missing") == ()
    assert store.payload("missing") == []


def test_record_keeps_turns_in_order_for_the_session() -> None:
    store = SessionStore()

    store.record("s1", _turn("first"))
    store.record("s1", _turn("second", route="task"))
    store.record("other", _turn("unrelated"))

    assert [turn.message for turn in store.history("s1")] == ["first", "second"]
    assert [turn.message for turn in store.history("other")] == ["unrelated"]


def test_record_trims_each_side_to_its_cap() -> None:
    store = SessionStore()

    store.record("s1", _turn("m" * 5_000, "r" * 5_000))

    turn = store.history("s1")[0]
    assert len(turn.message) == MAX_MESSAGE_CHARS
    assert turn.message.endswith("…")
    assert len(turn.reply) == MAX_REPLY_CHARS
    assert turn.reply.endswith("…")


def test_record_bounds_the_turn_count_oldest_first() -> None:
    store = SessionStore(max_turns=3)

    for index in range(5):
        store.record("s1", _turn(f"turn-{index}"))

    assert [turn.message for turn in store.history("s1")] == ["turn-2", "turn-3", "turn-4"]


def test_payload_renders_the_most_recent_turns_as_dicts() -> None:
    store = SessionStore()

    for index in range(4):
        store.record("s1", _turn(f"m{index}", f"r{index}", route="chat" if index else "task"))

    assert store.payload("s1", max_turns=2) == [
        {"user": "m2", "agent": "r2", "route": "chat"},
        {"user": "m3", "agent": "r3", "route": "chat"},
    ]


def test_record_evicts_the_oldest_session_beyond_the_session_cap() -> None:
    store = SessionStore(max_sessions=2)

    store.record("s1", _turn())
    store.record("s2", _turn())
    store.record("s3", _turn())

    assert store.history("s1") == ()
    assert len(store.history("s2")) == 1
    assert len(store.history("s3")) == 1


def test_record_refreshes_session_recency() -> None:
    store = SessionStore(max_sessions=2)

    store.record("s1", _turn())
    store.record("s2", _turn())
    store.record("s1", _turn("again"))
    store.record("s3", _turn())

    assert store.history("s2") == ()
    assert [turn.message for turn in store.history("s1")] == ["hi", "again"]


def test_store_rejects_invalid_limits() -> None:
    try:
        SessionStore(max_turns=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_turns=0 must be rejected")
    try:
        SessionStore(max_sessions=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_sessions=0 must be rejected")
