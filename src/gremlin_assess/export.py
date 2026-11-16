"""Build a candidate workspace from the public part of a package.

The package stores the flawed repo as plain files. This step gives the
candidate's copy a git history of exactly one commit, so nothing in the
history can show what was changed. See docs/specs/01, rule 5.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from gremlin_assess.package import Package

_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc")

# Fixed identity and date, so two exports of one package version are identical.
_GIT = [
    "git",
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "commit.gpgsign=false",
    "-c", "user.name=dev",
    "-c", "user.email=dev@example.invalid",
]
_FIXED_DATE = "2024-01-01T00:00:00+00:00"


class ExportError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_DATE=_FIXED_DATE, GIT_COMMITTER_DATE=_FIXED_DATE)
    try:
        result = subprocess.run([*_GIT, *args], cwd=repo, capture_output=True, text=True, env=env)
    except FileNotFoundError as exc:
        raise ExportError("git was not found on this machine") from exc
    if result.returncode != 0:
        raise ExportError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD"))


def export_public(package: Package, dest: Path) -> Path:
    """Create the workspace at `dest` and return the path of the repo inside it."""
    if dest.exists() and any(dest.iterdir()):
        raise ExportError(f"{dest} already exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)

    repo = dest / package.meta.candidate_name
    shutil.copytree(package.repo_dir, repo, ignore=_IGNORE)
    for name in ("BRIEF.md", "FINDINGS.yaml"):
        shutil.copy2(package.public_dir / name, dest / name)

    # An empty template stops hooks from the machine's git setup being copied in.
    _git(repo, "init", "-q", "--template=", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    return repo
