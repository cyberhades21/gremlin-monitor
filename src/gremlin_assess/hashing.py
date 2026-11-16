"""Content hash of a package version. See docs/specs/01, rule 3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

# Written after validation, so they cannot be part of what is hashed.
_SELF_REFERENTIAL_KEYS = ("content_hash", "image_digest")
_SKIPPED_DIRS = {"validation", "__pycache__", ".pytest_cache"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _meta_digest(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in _SELF_REFERENTIAL_KEYS:
        data.pop(key, None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(root: Path) -> str:
    """SHA-256 over a sorted list of every file's path and hash."""
    entries = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _SKIPPED_DIRS & set(relative.parts) or path.suffix == ".pyc":
            continue
        name = relative.as_posix()
        digest = _meta_digest(path) if name == "package.yaml" else file_sha256(path)
        entries.append(f"{name}\0{digest}\n")
    entries.sort()
    return "sha256:" + hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()
