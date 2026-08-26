from pathlib import Path

import pytest

from app.intelligence.python_analyzer import PythonProjectAnalyzer


def test_analyzer_extracts_python_files_and_imports(tmp_path: Path) -> None:
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "main.py").write_text(
        "import os\nfrom .helpers import run, validate\n",
        encoding="utf-8",
    )
    (tmp_path / "package" / "helpers.py").write_text("import json\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("import secrets\n", encoding="utf-8")

    index = PythonProjectAnalyzer().analyze(tmp_path)

    assert [file.path.as_posix() for file in index.files] == [
        "package/helpers.py",
        "package/main.py",
    ]
    assert index.files[1].imports[0].module == "os"
    assert index.files[1].imports[1].module == "helpers"
    assert index.files[1].imports[1].names == ("run", "validate")
    assert index.files[1].imports[1].relative_level == 1
    assert index.issues == ()


def test_analyzer_reports_syntax_errors_without_stopping_the_scan(tmp_path: Path) -> None:
    (tmp_path / "valid.py").write_text("import pathlib\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    index = PythonProjectAnalyzer().analyze(tmp_path)

    assert [file.path.as_posix() for file in index.files] == ["valid.py"]
    assert index.issues[0].path == Path("broken.py")
    assert index.issues[0].line == 1


def test_analyzer_extracts_top_level_symbols_and_internal_module_relationships(
    tmp_path: Path,
) -> None:
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "package" / "helpers.py").write_text(
        "def run():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "package" / "main.py").write_text(
        "from . import helpers\n"
        "from .helpers import run\n"
        "import package.helpers\n\n"
        "VALUE = 1\n"
        "class Service:\n    pass\n"
        "async def execute():\n    return await run()\n",
        encoding="utf-8",
    )

    index = PythonProjectAnalyzer().analyze(tmp_path)
    main_file = next(file for file in index.files if file.module_name == "package.main")

    assert [(symbol.name, symbol.kind) for symbol in main_file.symbols] == [
        ("VALUE", "variable"),
        ("Service", "class"),
        ("execute", "async_function"),
    ]
    assert [
        (relationship.source_module, relationship.target_module, relationship.line)
        for relationship in index.relationships
    ] == [
        ("package.main", "package.helpers", 1),
        ("package.main", "package.helpers", 2),
        ("package.main", "package.helpers", 3),
    ]


def test_analyzer_requires_an_existing_target_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing directory"):
        PythonProjectAnalyzer().analyze(tmp_path / "missing")
