"""CI quality gates must target the submitted change, not historical debt."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.check_changed_python_format import changed_python_files

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_changed_python_format.py"
WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@test.invalid")
    _git(repo, "config", "user.name", "CI Test")


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_changed_python_files_include_commit_worktree_and_untracked_without_deleted(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
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


def test_gate_fails_closed_when_requested_base_is_missing_from_shallow_clone(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    clone = tmp_path / "clone"
    _init_repo(source)
    (source / "base.py").write_text("value = 1\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    base = _git(source, "rev-parse", "HEAD")
    (source / "changed.py").write_text("value = 2\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "change")
    _git(tmp_path, "clone", "--depth", "1", source.as_uri(), str(clone))
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{base}^{{commit}}"],
            cwd=clone,
            check=False,
        ).returncode
        != 0
    )

    result = _run_gate(clone, "--base", base)

    assert result.returncode != 0
    assert "base revision is unavailable" in result.stderr.lower()


def test_gate_without_base_or_parent_fails_closed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "initial.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    result = _run_gate(tmp_path)

    assert result.returncode != 0
    assert "base revision is unavailable" in result.stderr.lower()


def test_gate_succeeds_only_for_real_zero_python_diff(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "existing.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "notes.md").write_text("documentation only\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "docs")

    result = _run_gate(tmp_path, "--base", base)

    assert result.returncode == 0
    assert "No changed Python files" in result.stdout


def test_backend_format_job_fetches_history_and_selects_event_specific_base() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    backend_job = workflow.split("\n  backend:", maxsplit=1)[1].split(
        "\n  migration-postgres:",
        maxsplit=1,
    )[0]

    assert "fetch-depth: 0" in backend_job
    assert (
        "github.event_name == 'pull_request' "
        "&& github.event.pull_request.base.sha || github.event.before"
    ) in backend_job
