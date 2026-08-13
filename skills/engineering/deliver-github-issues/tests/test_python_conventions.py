from __future__ import annotations

from pathlib import Path

PROJECT = Path(__file__).parents[1]


def test_uv_project_follows_repository_python_conventions() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    documentation = (
        PROJECT.parents[2] / "docs" / "engineering" / "deliver-github-issues.md"
    ).read_text(encoding="utf-8")

    assert 'requires-python = ">=3.12"' in pyproject
    assert (PROJECT / "uv.lock").is_file()
    assert "uv run --project" in documentation
    assert "--locked deliver-github-issues" in documentation
