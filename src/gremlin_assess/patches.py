"""Make and apply reference patches. One patch per checklist item."""

from __future__ import annotations

import difflib
import subprocess
from collections.abc import Sequence
from pathlib import Path

IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv"}

# Settings that would otherwise come from the machine's own git config and
# change the bytes written to disk.
GIT = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]


class PatchError(Exception):
    pass


def tree_files(root: Path) -> dict[str, Path]:
    """Every file under root, keyed by its posix path, skipping caches and git data."""
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or IGNORED_DIRS & set(relative.parts) or path.suffix == ".pyc":
            continue
        files[relative.as_posix()] = path
    return files


def _lines(path: Path | None) -> list[str]:
    if path is None:
        return []
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchError(f"{path}: binary files cannot be patched") from exc
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def make_patch(base: Path, changed: Path) -> str:
    """Unified diff that turns the tree at `base` into the tree at `changed`."""
    base_files, changed_files = tree_files(base), tree_files(changed)
    chunks: list[str] = []
    for name in sorted(set(base_files) | set(changed_files)):
        old, new = _lines(base_files.get(name)), _lines(changed_files.get(name))
        if old == new:
            continue
        from_name = f"a/{name}" if name in base_files else "/dev/null"
        to_name = f"b/{name}" if name in changed_files else "/dev/null"
        for line in difflib.unified_diff(old, new, from_name, to_name):
            if line.endswith("\n"):
                chunks.append(line)
            else:
                chunks.append(line + "\n\\ No newline at end of file\n")
    return "".join(chunks)


def apply_patches(repo: Path, patches: Sequence[Path]) -> None:
    """Apply patches in order to the plain directory `repo`."""
    for patch in patches:
        if not patch.is_file():
            raise PatchError(f"{patch.name}: patch file is missing")
        result = subprocess.run(
            [*GIT, "apply", "--whitespace=nowarn", str(patch.resolve())],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PatchError(f"{patch.name} does not apply: {result.stderr.strip()}")
