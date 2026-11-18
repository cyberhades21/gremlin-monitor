"""The reference state of a package: the flawed repo with reference patches applied."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from gremlin_assess.hashing import tree_sha256
from gremlin_assess.package import Package
from gremlin_assess.patches import PatchError, apply_patches

_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")


def patch_order(package: Package, item_ids: Iterable[str]) -> list[str]:
    """Item ids with every dependency placed before the item that needs it."""
    ordered: list[str] = []

    def visit(item_id: str, trail: tuple[str, ...]) -> None:
        if item_id in ordered:
            return
        if item_id in trail:
            raise PatchError(f"{item_id}: depends_on forms a loop")
        for dependency in package.item(item_id).depends_on:
            visit(dependency, (*trail, item_id))
        ordered.append(item_id)

    for item_id in item_ids:
        visit(item_id, ())
    return ordered


def copy_repo(package: Package, dest: Path, item_ids: Iterable[str]) -> Path:
    """Copy the flawed repo to `dest` and apply the reference patches of `item_ids`."""
    shutil.copytree(package.repo_dir, dest, ignore=_IGNORE)
    patches = [package.patch_for(i) for i in patch_order(package, item_ids) if package.patch_for(i).is_file()]
    apply_patches(dest, patches)
    return dest


def all_item_ids(package: Package) -> list[str]:
    return [item.id for item in package.checklist]


def reference_hash(package: Package) -> str:
    """Hash of the repo with every reference patch applied.

    It changes when the public repo or a patch changes, and not when only
    `extras.yaml` or a sign-off changes, so an audit stays valid across those.
    """
    with tempfile.TemporaryDirectory(prefix="reference-", ignore_cleanup_errors=True) as tmp:
        repo = copy_repo(package, Path(tmp) / "repo", all_item_ids(package))
        return "sha256:" + tree_sha256(repo)
