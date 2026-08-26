from pathlib import Path

from app.intelligence.models import (
    ModuleRelationship,
    PythonFileInfo,
    PythonProjectIndex,
    PythonSymbol,
)
from app.intelligence.summary import PythonProjectSummarizer


def test_summarizer_includes_modules_symbols_and_relationships(tmp_path: Path) -> None:
    index = PythonProjectIndex(
        target_root=tmp_path,
        files=(
            PythonFileInfo(
                path=Path("app/main.py"),
                module_name="app.main",
                is_package=False,
                imports=(),
                symbols=(PythonSymbol(name="run", kind="function", line=1),),
            ),
        ),
        relationships=(ModuleRelationship("app.main", "app.service", 1),),
        issues=(),
    )

    summary = PythonProjectSummarizer().summarize(index)

    assert "app.main (app/main.py): function run" in summary
    assert "app.main -> app.service" in summary


def test_summarizer_respects_its_character_limit(tmp_path: Path) -> None:
    index = PythonProjectIndex(
        target_root=tmp_path,
        files=(
            PythonFileInfo(
                path=Path("module.py"),
                module_name="module",
                is_package=False,
                imports=(),
                symbols=tuple(
                    PythonSymbol(name=f"symbol_{index}", kind="function", line=index)
                    for index in range(20)
                ),
            ),
        ),
        relationships=(),
        issues=(),
    )

    summary = PythonProjectSummarizer(max_characters=100).summarize(index)

    assert len(summary) == 100
    assert summary.endswith("…")
