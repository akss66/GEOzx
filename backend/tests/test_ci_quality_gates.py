"""CI quality gates must target the submitted change, not historical debt."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.check_changed_python_format import changed_python_files


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_changed_python_files_include_commit_worktree_and_untracked_without_deleted(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "ci@test.invalid")
    _git(tmp_path, "config", "user.name", "CI Test")
    (tmp_path / "existing.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "deleted.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    (tmp_path / "existing.py").write_text("value=3\n", encoding="utf-8")
    (tmp_path / "committed.py").write_text("value=4\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("changed\n", encoding="utf-8")
    (tmp_path / "deleted.py").unlink()
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "submitted change")
    (tmp_path / "working.py").write_text("value=5\n", encoding="utf-8")

    assert changed_python_files(tmp_path, base=base) == [
        "committed.py",
        "existing.py",
        "working.py",
    ]
