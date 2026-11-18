"""Submit your work.

Checks that FINDINGS.yaml is well formed, then saves a copy of the workspace.
You can submit more than once. The last submission is the one that counts.
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from workspace_tools.findings_check import check_findings_text

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "node_modules"}
FINDINGS_FILE = "FINDINGS.yaml"
MARKER_FILE = "SUBMITTED"


def _keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = Path(info.name).parts
    if any(part in SKIP_DIRS for part in parts) or info.name.endswith(".pyc"):
        return None
    return info


def make_archive(workspace: Path, archive: Path) -> None:
    """Write a compressed copy of a workspace, without caches."""
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(workspace, arcname="workspace", filter=_keep)


def submit(workspace: Path, out_dir: Path) -> tuple[Path | None, list[str]]:
    """Check the findings file and archive the workspace. Returns (archive, problems)."""
    findings_path = workspace / FINDINGS_FILE
    if not findings_path.is_file():
        return None, [f"{FINDINGS_FILE} is missing from {workspace}"]
    _, problems = check_findings_text(findings_path.read_text(encoding="utf-8"))
    if problems:
        return None, problems

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = out_dir / f"submission-{stamp}.tar.gz"
    make_archive(workspace, archive)
    (out_dir / MARKER_FILE).write_text(f"{stamp} {archive.name}\n", encoding="utf-8")
    return archive, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="submit", description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="folder holding FINDINGS.yaml")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("SUBMIT_DIR", "/submissions")),
        help="where the submission is saved",
    )
    args = parser.parse_args(argv)

    archive, problems = submit(args.workspace.resolve(), args.out)
    if problems:
        print("Your findings file needs fixing before you can submit:")
        for problem in problems:
            print(f"  - {problem}")
        print("Fix these and run submit again.")
        return 1
    print(f"Submitted: {archive.name}")
    print("You can submit again if you make more changes. The last submission counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
