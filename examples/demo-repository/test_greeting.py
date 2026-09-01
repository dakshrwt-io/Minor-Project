from greeting import greet


def test_greeting() -> None:
    assert greet() == "Hello, World!"
