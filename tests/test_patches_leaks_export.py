import shutil
import subprocess

import pytest

from gremlin_assess.export import ExportError, commit_count, export_public
from gremlin_assess.leakscan import scan_public
from gremlin_assess.patches import PatchError, apply_patches, make_patch, tree_files


def _contents(root):
    return {name: path.read_bytes() for name, path in tree_files(root).items()}


def test_patch_round_trip(tmp_path):
    base, changed, target = tmp_path / "base", tmp_path / "changed", tmp_path / "target"
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "a.py").write_bytes(b"one\ntwo\nthree\n")
    (base / "gone.txt").write_bytes(b"remove me\n")
    (base / "tail.txt").write_bytes(b"no newline at end")
    shutil.copytree(base, changed)
    shutil.copytree(base, target)

    (changed / "pkg" / "a.py").write_bytes(b"one\n2\nthree\nfour\n")
    (changed / "gone.txt").unlink()
    (changed / "new.txt").write_bytes(b"hello\n")
    (changed / "tail.txt").write_bytes(b"still no newline")

    patch = tmp_path / "change.patch"
    patch.write_bytes(make_patch(base, changed).encode("utf-8"))
    apply_patches(target, [patch])
    assert _contents(target) == _contents(changed)


def test_identical_trees_make_an_empty_patch(toy):
    assert make_patch(toy.repo_dir, toy.repo_dir) == ""


def test_a_patch_that_does_not_apply_raises(toy, repo_with):
    repo = repo_with("G-001")
    with pytest.raises(PatchError, match="G-001.patch does not apply"):
        apply_patches(repo, [toy.patch_for("G-001")])


def test_public_part_of_toy_is_clean(toy):
    assert scan_public(toy.public_dir, ["G-001", "RC-001", "pkg-toy"]) == []


def test_internal_words_ids_and_caches_are_caught(toy_copy):
    public = toy_copy / "public"
    (public / "repo" / "docs" / "notes.md").write_text("see G-001 in the Gremlin list\nPG-0012 is fine\n", encoding="utf-8")
    cache = public / "repo" / "tallybook" / "__pycache__"
    cache.mkdir()
    (cache / "limits.cpython-311.pyc").write_bytes(b"\0")
    (public / "repo" / "README.md.orig").write_text("old\n", encoding="utf-8")

    leaks = scan_public(public, ["G-001"])
    found = {(leak.path, leak.line, leak.reason) for leak in leaks}
    assert ("repo/docs/notes.md", 1, "contains 'gremlin'") in found
    assert ("repo/docs/notes.md", 1, "contains internal id 'G-001'") in found
    assert not any(leak.line == 2 for leak in leaks)
    assert [leak.path for leak in leaks if leak.line is None] == ["repo/README.md.orig", "repo/tallybook/__pycache__"]


def test_export_makes_one_commit_and_ships_nothing_hidden(toy, tmp_path):
    workspace = tmp_path / "workspace"
    repo = export_public(toy, workspace)

    assert commit_count(repo) == 1
    assert sorted(path.name for path in workspace.iterdir()) == ["BRIEF.md", "FINDINGS.yaml", "tallybook"]
    shipped = [path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if ".git" not in path.parts]
    assert "tallybook/tallybook/limits.py" in shipped
    assert not any("hidden" in name or name.endswith((".patch", "checklist.yaml", "package.yaml")) for name in shipped)


def test_two_exports_are_the_same_commit(toy, tmp_path):
    def head(repo):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

    first = head(export_public(toy, tmp_path / "one"))
    assert len(first) == 40
    assert head(export_public(toy, tmp_path / "two")) == first


def test_export_refuses_a_destination_with_files(toy, tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ExportError, match="not empty"):
        export_public(toy, tmp_path / "busy")
