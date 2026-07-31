"""Check Ruff formatting only for Python files changed by the submitted revision."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


class BaseRevisionUnavailable(RuntimeError):
    """The requested comparison range cannot be proven from local history."""


def changed_python_files(repo: Path, *, base: str | None) -> list[str]:
    repo = repo.resolve()
    candidates: set[str] = set()
    effective_base = _effective_base(repo, base)
    if effective_base is not None:
        candidates.update(
            _git_lines(
                repo,
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                f"{effective_base}...HEAD",
                "--",
                "*.py",
            )
        )
    candidates.update(
        _git_lines(
            repo,
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "--cached",
            "--",
            "*.py",
        )
    )
    candidates.update(
        _git_lines(
            repo,
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "--",
            "*.py",
        )
    )
    candidates.update(
        item
        for item in _git_lines(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "*.py",
        )
        if item.endswith(".py")
    )
    return sorted(item for item in candidates if (repo / item).is_file())


def _effective_base(repo: Path, requested: str | None) -> str:
    requested = (requested or "").strip()
    if requested and set(requested) != {"0"}:
        if _is_commit(repo, requested):
            return requested
        raise BaseRevisionUnavailable(
            f"Base revision is unavailable: {requested}. "
            "Fetch the comparison history before running the format gate."
        )
    if _is_commit(repo, "HEAD^"):
        return "HEAD^"
    raise BaseRevisionUnavailable(
        "Base revision is unavailable and HEAD has no locally available parent. "
        "Pass a valid --base or fetch the comparison history."
    )


def _is_commit(repo: Path, revision: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _git_lines(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=None)
    args = parser.parse_args()
    repo = _repo_root(Path.cwd())
    try:
        files = changed_python_files(repo, base=args.base)
    except BaseRevisionUnavailable as exc:
        print(f"Changed-Python format gate refused to run: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("No changed Python files require Ruff format validation.")
        return 0
    print("Checking changed Python files:")
    for path in files:
        print(f"  {path}")
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", "--", *files],
        cwd=repo,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
