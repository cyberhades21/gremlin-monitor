"""Build the anonymised packets reviewers receive. See docs/specs/05 and 12.

Step 1 holds the work and no checklist. Step 2 holds the checklist and the
rubric anchors, and is sent only after step 1 comes back. Neither holds our
results, the candidate's name or experience band, or another reviewer's answers.
"""

from __future__ import annotations

import csv
import secrets
import shutil
from collections.abc import Iterable
from pathlib import Path

from gremlin_assess.package import Package
from gremlin_assess.patches import make_patch
from gremlin_assess.reviews import PROCESS_IDS, PROCESS_NAMES, step1_template, step2_template
from gremlin_assess.session import SessionError, load_meta
from workspace_tools.findings_check import check_findings_text

# Files that hold our own results or identity and must never reach a reviewer.
NEVER_COPIED = ("meta.yaml", "grading", "report.md", "walkthrough.md", "candidates.csv")
ASSIGNMENTS = "assignments.csv"
ASSIGNMENT_COLUMNS = ["packet", "session", "reviewer"]
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".txt", ".patch", ".log", ".cast", ".json", ".py", ".csv", ".timing"}


class PacketError(Exception):
    pass


def new_packet_id() -> str:
    return f"P-{secrets.token_hex(3)}"


def check_packet(directory: Path, forbidden: Iterable[str]) -> list[str]:
    """Search every file in a packet for strings that must not be there. Empty means clean."""
    terms = [term for term in forbidden if term and term.strip()]
    problems = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.name in NEVER_COPIED or path.name.startswith("meta."):
            problems.append(f"{relative}: this file must never be in a packet")
        if not path.is_file():
            continue
        haystack = path.read_bytes().decode("utf-8", errors="ignore").lower()
        for term in terms:
            if term.lower() in haystack or term.lower() in relative.lower():
                problems.append(f"{relative}: contains '{term}'")
    return problems


def _copy_if_present(source: Path, dest: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, dest)
    elif source.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)


def gremlin_list_markdown(package: Package) -> str:
    lines = ["# Checklist", "", "Judge each item against the final code. `yes` means fully done, `partly` means some of it, `no` means not done.", ""]
    for item in package.checklist:
        lines.append(f"## {item.id}: {item.summary}")
        if item.kind == "gremlin":
            lines.append(f"- Kind: planted issue, category {item.category}")
            lines.append(f"- What is right: {item.truth_statement}")
            lines.append("- Where it sits: " + ", ".join(f"{loc.path}:{loc.lines}" for loc in item.evidence_location))
        else:
            lines.append("- Kind: task")
        lines.append("")
    if package.rubric:
        lines += ["# Rubric", "", "Score each criterion from 0 to 3 using its anchors.", ""]
        for criterion in package.rubric:
            lines.append(f"## {criterion.id} (item {criterion.item}): {criterion.criterion}")
            lines += [f"- {score}: {criterion.anchors[score]}" for score in range(4)]
            lines.append("")
    lines += ["# Process ratings", "", "Rate each as observed, not_observed or cannot_tell. For observed, give a time in the recording or a line in the transcript.", ""]
    lines += [f"- {pid}: {PROCESS_NAMES[pid]}" for pid in PROCESS_IDS]
    return "\n".join(lines) + "\n"


def build_packet(
    root: Path,
    session_id: str,
    package: Package,
    reviewer: str,
    out_dir: Path,
    forbidden: Iterable[str],
) -> str:
    """Write step 1 and step 2 folders for one reviewer and return the packet id.

    `forbidden` is every string that identifies the candidate: name, email, handles.
    Nothing is left behind when a check fails.
    """
    session_dir = Path(root) / "sessions" / session_id
    meta = load_meta(session_dir)
    final = session_dir / "final"
    repo = final / package.meta.candidate_name
    if not repo.is_dir():
        raise PacketError(f"{session_id} has no collected repo folder")
    if meta.package_id != package.meta.id:
        raise PacketError(f"{session_id} used package {meta.package_id}, not {package.meta.id}")

    packet_id = new_packet_id()
    step1, step2 = out_dir / packet_id / "step1", out_dir / packet_id / "step2"
    step1.mkdir(parents=True)
    step2.mkdir(parents=True)

    shutil.copyfile(package.public_dir / "BRIEF.md", step1 / "BRIEF.md")
    (step1 / "final-changes.patch").write_bytes(make_patch(package.repo_dir, repo).encode("utf-8"))
    _copy_if_present(session_dir / "FINDINGS.yaml", step1 / "FINDINGS.yaml")
    _copy_if_present(final / "ARCHITECTURE.md", step1 / "ARCHITECTURE.md")
    _copy_if_present(session_dir / "transcript", step1 / "transcript")
    for recording in session_dir.glob("terminal.*"):
        shutil.copyfile(recording, step1 / recording.name)
    (step1 / "REVIEW-step1.yaml").write_bytes(step1_template(packet_id).encode("utf-8"))

    (step2 / "GREMLIN-LIST.md").write_bytes(gremlin_list_markdown(package).encode("utf-8"))
    (step2 / "REVIEW-step2.yaml").write_bytes(step2_template(package, packet_id).encode("utf-8"))

    problems = check_packet(step1, forbidden) + check_packet(step2, forbidden)
    if problems:
        shutil.rmtree(out_dir / packet_id)
        raise PacketError("the packet was not written:\n  " + "\n  ".join(problems))

    record_assignment(Path(root) / "reviews", packet_id, session_id, reviewer)
    return packet_id


def record_assignment(reviews_dir: Path, packet_id: str, session_id: str, reviewer: str) -> None:
    """The only link between a packet and its session. Kept apart from the packet."""
    reviews_dir.mkdir(parents=True, exist_ok=True)
    path = reviews_dir / ASSIGNMENTS
    new = not path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSIGNMENT_COLUMNS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({"packet": packet_id, "session": session_id, "reviewer": reviewer})


def load_assignments(reviews_dir: Path) -> dict[str, tuple[str, str]]:
    """Packet id -> (session id, reviewer)."""
    path = Path(reviews_dir) / ASSIGNMENTS
    if not path.is_file():
        raise SessionError(f"{path} is missing")
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["packet"]: (row["session"], row["reviewer"]) for row in csv.DictReader(handle)}
