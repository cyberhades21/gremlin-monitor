"""Session folders for the manual pilot. See docs/specs/12.

All session data stays outside git, under the pilot directory.
"""

from __future__ import annotations

import csv
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gremlin_assess.package import Package
from workspace_tools.findings_check import check_findings_text
from workspace_tools.submit import make_archive

SESSION_ID = re.compile(r"^S-\d{3}$")
CANDIDATE_CODE = re.compile(r"^C-\d{2}$")
TRACKER = "tracker.csv"
TRACKER_COLUMNS = ["session", "candidate", "package", "agent_source", "status", "start", "end", "walkthrough", "survey"]
SUBDIRS = ("transcript", "snapshots", "grading")

# What must exist at each stage. The recording is any file named terminal.*
STAGES = {
    "collected": ["meta.yaml", "final", "FINDINGS.yaml", "transcript", "snapshots"],
    "graded": ["grading/grade.json", "grading/matches.json", "report.md"],
    "complete": ["walkthrough.md"],
}


class SessionError(Exception):
    pass


class Intervention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: str
    note: str


class SessionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: str
    candidate_code: str
    package_id: str
    package_version: str
    agent_config: str
    agent_source: Literal["ours", "own-claude-code", "own-codex"] = "ours"
    key_id: str | None = None
    start: str | None = None
    end: str | None = None
    status: Literal["scheduled", "running", "submitted", "graded", "withdrawn", "discarded"] = "scheduled"
    interventions: list[Intervention] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def session_path(root: Path, session_id: str) -> Path:
    if not SESSION_ID.match(session_id):
        raise SessionError(f"'{session_id}' is not a session id like S-001")
    return Path(root) / "sessions" / session_id


# --- meta and tracker -------------------------------------------------------


def load_meta(directory: Path) -> SessionMeta:
    path = directory / "meta.yaml"
    if not path.is_file():
        raise SessionError(f"{path} is missing")
    try:
        return SessionMeta.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValidationError, yaml.YAMLError) as exc:
        raise SessionError(f"{path} could not be read: {exc}") from exc


def save_meta(directory: Path, meta: SessionMeta) -> None:
    text = yaml.safe_dump(meta.model_dump(), sort_keys=False, allow_unicode=True)
    (directory / "meta.yaml").write_bytes(text.encode("utf-8"))


def update_tracker(root: Path, session_id: str, **fields: str) -> None:
    """Add or update one row of tracker.csv. Unknown columns are an error."""
    unknown = set(fields) - set(TRACKER_COLUMNS)
    if unknown:
        raise SessionError(f"tracker has no column {sorted(unknown)[0]}")
    path = Path(root) / TRACKER
    rows: list[dict[str, str]] = []
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    row = next((r for r in rows if r["session"] == session_id), None)
    if row is None:
        row = dict.fromkeys(TRACKER_COLUMNS, "")
        row["session"] = session_id
        rows.append(row)
    row.update({key: str(value) for key, value in fields.items()})
    rows.sort(key=lambda r: r["session"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACKER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


# --- lifecycle --------------------------------------------------------------


def new_session(
    root: Path,
    session_id: str,
    package: Package,
    candidate_code: str,
    *,
    agent_source: str = "ours",
    key_id: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Create the folder for a session and its meta file."""
    if not CANDIDATE_CODE.match(candidate_code):
        raise SessionError(f"'{candidate_code}' is not a candidate code like C-01")
    directory = session_path(root, session_id)
    if directory.exists():
        raise SessionError(f"{session_id} already exists")
    directory.mkdir(parents=True)
    for name in SUBDIRS:
        (directory / name).mkdir()
    meta = SessionMeta(
        session=session_id,
        candidate_code=candidate_code,
        package_id=package.meta.id,
        package_version=package.meta.version,
        agent_config=package.meta.agent_config,
        agent_source=agent_source,
        key_id=key_id,
    )
    save_meta(directory, meta)
    update_tracker(
        root,
        session_id,
        candidate=candidate_code,
        package=f"{package.meta.id}@{package.meta.version}",
        agent_source=agent_source,
        status="scheduled",
    )
    return directory


def start_session(root: Path, session_id: str, now: datetime | None = None) -> None:
    directory = session_path(root, session_id)
    meta = load_meta(directory)
    meta.status, meta.start = "running", _stamp(now or utc_now())
    save_meta(directory, meta)
    update_tracker(root, session_id, status="running", start=meta.start)


def add_intervention(root: Path, session_id: str, note: str, now: datetime | None = None) -> None:
    directory = session_path(root, session_id)
    meta = load_meta(directory)
    meta.interventions.append(Intervention(time=_stamp(now or utc_now()), note=note))
    save_meta(directory, meta)


def write_walkthrough(root: Path, session_id: str, result: str, notes: str = "") -> Path:
    """Record the walkthrough. The first line is the result, which the report reads."""
    if result not in ("consistent", "doubtful", "not done"):
        raise SessionError("the walkthrough result must be 'consistent', 'doubtful' or 'not done'")
    if result == "doubtful" and not notes.strip():
        raise SessionError("a 'doubtful' result needs notes saying what did not fit")
    directory = session_path(root, session_id)
    load_meta(directory)
    path = directory / "walkthrough.md"
    path.write_bytes(f"Result: {result}\n\n{notes.strip()}\n".encode("utf-8"))
    update_tracker(root, session_id, walkthrough=result)
    return path


def snapshot(workspace: Path, session_dir: Path, now: datetime | None = None) -> Path:
    """Save a compressed copy of the live workspace into the session's snapshots folder."""
    if not workspace.is_dir():
        raise SessionError(f"{workspace} is not a directory")
    name = (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")
    archive = session_dir / "snapshots" / f"{name}.tar.gz"
    make_archive(workspace, archive)
    return archive


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract a tar file that came from a candidate, refusing anything that could escape `dest`."""
    root = dest.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if root != target and root not in target.parents:
                raise SessionError(f"the submission holds a path outside its folder: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise SessionError(f"the submission holds a link or special file: {member.name}")
        for member in tar.getmembers():
            tar.extract(member, dest)


def collect(root: Path, session_id: str, submission: Path, now: datetime | None = None) -> list[str]:
    """Move a submitted workspace into the session folder. Returns problems, empty when it is usable.

    `submission` is the archive written by `submit`, or an unpacked workspace folder.
    """
    directory = session_path(root, session_id)
    meta = load_meta(directory)
    final = directory / "final"
    if final.exists():
        raise SessionError(f"{session_id} already has a final folder")

    if submission.is_dir():
        shutil.copytree(submission, final)
    else:
        with tempfile.TemporaryDirectory(prefix="collect-") as tmp:
            _safe_extract(submission, Path(tmp))
            unpacked = Path(tmp) / "workspace"
            shutil.copytree(unpacked if unpacked.is_dir() else Path(tmp), final)

    problems = []
    findings = final / "FINDINGS.yaml"
    if not findings.is_file():
        problems.append("the submission has no FINDINGS.yaml")
    else:
        _, findings_problems = check_findings_text(findings.read_text(encoding="utf-8"))
        problems.extend(f"FINDINGS.yaml: {p}" for p in findings_problems)
        shutil.copyfile(findings, directory / "FINDINGS.yaml")
    if not any(path.is_dir() and path.name != "__pycache__" for path in final.iterdir()):
        problems.append("the submission holds no repo folder")

    meta.status, meta.end = "submitted", _stamp(now or utc_now())
    save_meta(directory, meta)
    update_tracker(root, session_id, status="submitted", end=meta.end)
    return problems


def missing_artifacts(session_dir: Path, stage: str) -> list[str]:
    """Files still missing for a stage ('collected', 'graded' or 'complete'). Earlier stages count too."""
    names: list[str] = []
    for name in ("collected", "graded", "complete"):
        names += STAGES[name]
        if name == stage:
            break
    else:
        raise SessionError(f"unknown stage '{stage}'")

    missing = []
    for name in names:
        path = session_dir / name
        if not path.exists() or (path.is_dir() and not any(path.iterdir())):
            missing.append(name)
    if stage in ("collected", "graded", "complete") and not any(session_dir.glob("terminal.*")):
        missing.append("terminal.* (the terminal recording)")
    return missing
