"""Tests for documentation search (RAG), with a fake embedding model (no internet needed)."""

import os

from app import config, rag
from app.tools import run_tool

# The fake model gives each text 3 numbers: how much it is about logging in,
# about installing, and about databases. Real models do the same idea with 1536 numbers.
TOPICS = [
    {"login", "log", "sign", "password", "passwords"},
    {"install", "setup", "pip"},
    {"database", "sqlite", "table"},
]


def fake_embed(texts):
    vectors = []
    for text in texts:
        words = set(text.lower().replace(".", " ").replace("?", " ").split())
        vectors.append([len(words & topic) + 0.01 for topic in TOPICS])
    return vectors


def make_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VECTOR_DB", str(tmp_path / "vector_db"))
    monkeypatch.setattr(rag, "embed", fake_embed)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "auth.md").write_text("Users log in with a password.", encoding="utf-8")
    (repo / "docs" / "install.md").write_text("Run pip install to set up.", encoding="utf-8")
    return repo


def test_finds_doc_by_meaning(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)

    result = run_tool(repo, "search_docs", {"query": "how do I sign in?"}, False)

    assert result.startswith("docs/auth.md (from line 1, match")  # best match comes first


def test_changed_file_is_indexed_again(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    run_tool(repo, "search_docs", {"query": "install"}, False)  # builds the index

    auth = repo / "docs" / "auth.md"
    auth.write_text("Data lives in a sqlite database table.", encoding="utf-8")
    os.utime(auth, (1, 1))  # give it a different "last modified" time
    result = run_tool(repo, "search_docs", {"query": "which database?"}, False)

    assert result.startswith("docs/auth.md")
    assert "sqlite database" in result
    assert "password" not in result  # the old text was removed from the index


def test_only_docs_folder_is_searched(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "notes.md").write_text("database database database", encoding="utf-8")

    result = run_tool(repo, "search_docs", {"query": "database"}, False)

    assert "notes.md" not in result


def test_secret_files_are_never_embedded(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "docs" / ".env").write_text("PASSWORD=hunter2", encoding="utf-8")

    result = run_tool(repo, "search_docs", {"query": "password"}, False)

    assert "hunter2" not in result


def test_repo_without_docs_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VECTOR_DB", str(tmp_path / "vector_db"))

    result = run_tool(tmp_path, "search_docs", {"query": "anything"}, False)

    assert result == "This repository has no docs/ folder to search."
