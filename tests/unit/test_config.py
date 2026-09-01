from pathlib import Path

from app.config import Settings


def test_settings_load_key_from_dot_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        'ANTHROPIC_API_KEY="sk-ant-from-dotenv"\n# comment\nAGENT_MODEL=claude-sonnet\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.anthropic_api_key == "sk-ant-from-dotenv"
    assert settings.model_name == "claude-sonnet"


def test_real_environment_wins_over_dot_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.anthropic_api_key == "from-shell"


def test_settings_ignore_a_missing_dot_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.anthropic_api_key is None


def test_explicit_mapping_never_touches_dot_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env({"ANTHROPIC_API_KEY": "from-mapping"})

    assert settings.anthropic_api_key == "from-mapping"
