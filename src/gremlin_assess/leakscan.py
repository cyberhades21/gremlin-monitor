"""Scan the public part of a package for anything that gives answers away.

See docs/specs/06 (V11) and docs/specs/08 (T9).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_NAMES = {"__pycache__", ".pytest_cache", ".git", ".DS_Store", "Thumbs.db"}
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".bak", ".swp", "~")
# Internal vocabulary and paths that must never reach a candidate.
ALWAYS_FORBIDDEN_TEXT = (
    "gremlin",
    "hidden/tests",
    "checklist.yaml",
    "rubric.yaml",
    "extras.yaml",
    "walkthrough.yaml",
    "package.yaml",
)


@dataclass(frozen=True)
class Leak:
    path: str
    line: int | None
    reason: str


def _id_pattern(term: str) -> re.Pattern[str]:
    # Match the id as a whole token, so "G-001" does not fire inside "PG-0012".
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])")


def scan_public(public_dir: Path, internal_ids: Iterable[str]) -> list[Leak]:
    """Return every leak found under `public_dir`. An empty list means clean."""
    id_patterns = [(term, _id_pattern(term)) for term in sorted(set(internal_ids)) if term]
    leaks: list[Leak] = []

    for path in sorted(public_dir.rglob("*")):
        name = path.relative_to(public_dir).as_posix()
        if path.name in FORBIDDEN_NAMES:
            leaks.append(Leak(name, None, "caches, git data and system files must not ship"))
            continue
        if FORBIDDEN_NAMES & set(path.relative_to(public_dir).parts):
            continue  # already reported at the directory
        if "gremlin" in path.name.lower():
            leaks.append(Leak(name, None, "internal vocabulary in a file name"))
        if not path.is_file():
            continue
        if path.name.endswith(FORBIDDEN_SUFFIXES):
            leaks.append(Leak(name, None, "compiled, backup and patch-reject files must not ship"))
            continue

        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.split("\n"), start=1):
            lowered = line.lower()
            for term in ALWAYS_FORBIDDEN_TEXT:
                if term in lowered:
                    leaks.append(Leak(name, number, f"contains '{term}'"))
            for term, pattern in id_patterns:
                if pattern.search(line):
                    leaks.append(Leak(name, number, f"contains internal id '{term}'"))
    return leaks
