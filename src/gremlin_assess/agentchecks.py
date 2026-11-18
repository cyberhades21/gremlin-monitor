"""Agent-alone runs (V7) and the unlisted-flaw audit (V8). See docs/specs/06.

The validator never starts an agent. The operator, or a runner built after
spike 1, runs the pinned agent in the workspaces that `prepare_agent_alone`
and `prepare_audit` make. This module then reads what the agent left behind and
decides the two checks, so the checks work with whichever agent is chosen.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gremlin_assess.export import export_public
from gremlin_assess.grading.findings_llm import identified
from gremlin_assess.grading.harness import Runner
from gremlin_assess.grading.model_client import ModelClient
from gremlin_assess.grading.workspace import WorkspaceGrade, grade_workspace, write_json
from gremlin_assess.hashing import content_hash, file_sha256
from gremlin_assess.matching import MAX_DISTANCE, gap
from gremlin_assess.models import Acknowledgements, ChecklistItem, TriageEntry
from gremlin_assess.package import Package
from gremlin_assess.patches import PatchError, tree_files
from gremlin_assess.reference import reference_hash
from workspace_tools.findings_check import check_findings_text, parse_location

# Word for word from spec 06. A candidate-style brief, no follow-up messages.
AGENT_ALONE_PROMPT = "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml."
AUDIT_PROMPT = (
    "Review the repository in this folder. List every bug, documentation error and test error you can find, "
    "each with its location, in FINDINGS.yaml, and set the status of each to found_not_fixed. "
    "Do not change any other file."
)
AGENT_ALONE_RUNS = 5
AUDIT_RUNS = 3
MAX_FULL_SOLVES = 1

RUN_TEMPLATE = """\
# Fill this in when the run ends. See docs/specs/06.
agent_config: {agent_config}   # keep the package's pinned agent config
model:                         # the model id the agent reported, for example claude-opus-5-5
ended_by:                      # agent_stopped | time_limit | token_limit | error
duration_minutes:
input_tokens:
output_tokens:
cost_usd:
notes: ""
"""


class AgentCheckError(Exception):
    pass


class RunMeta(BaseModel):
    """`run.yaml`: what the operator or the runner records about one run."""

    model_config = ConfigDict(extra="forbid")

    agent_config: str
    model: str | None = None
    ended_by: Literal["agent_stopped", "time_limit", "token_limit", "error"] | None = None
    duration_minutes: float | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    notes: str = ""


@dataclass
class Evaluation:
    status: str  # pass | fail | not_run
    problems: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (id, text)
    table: dict | None = None  # goes into validation/report.json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- preparing runs -----------------------------------------------------------


def agent_input_hash(package: Package) -> str:
    """Hash of everything an agent sees or runs on: the public part and the settings that shape the run.

    The hidden part is not in it. It decides how a run is graded and not what the agent did,
    so changing a hidden test or `extras.yaml` means grading the same runs again, not making new ones.
    """
    meta = package.meta
    settings = {
        "candidate_name": meta.candidate_name,
        "agent_config": meta.agent_config,
        "session": meta.session.model_dump(mode="json"),
        "environment": meta.environment.model_dump(mode="json"),
    }
    entries = sorted(f"{name}\0{file_sha256(path)}\n" for name, path in tree_files(package.public_dir).items())
    digest = hashlib.sha256("".join(entries).encode("utf-8"))
    digest.update(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _current_reference_hash(package: Package) -> str:
    try:
        return reference_hash(package)
    except PatchError as exc:
        raise AgentCheckError(
            f"the reference state cannot be built, so fix the reference patches first (check V4): {exc}"
        ) from exc


def _prepare(package: Package, dest: Path, *, kind: str, runs: int, prompt: str, reference: bool, brief: bool) -> Path:
    if runs < 1:
        raise AgentCheckError("there must be at least one run")
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise AgentCheckError(f"{dest} already exists and is not empty")
    # Built first, so a package whose reference state is broken leaves nothing behind.
    subject_hash = _current_reference_hash(package) if kind == "audit" else None
    dest.mkdir(parents=True, exist_ok=True)

    for number in range(1, runs + 1):
        run = dest / f"run-{number}"
        export_public(package, run / "workspace", reference=reference, brief=brief)
        (run / "transcript").mkdir()
        (run / "run.yaml").write_bytes(RUN_TEMPLATE.format(agent_config=package.meta.agent_config).encode("utf-8"))
    (dest / "PROMPT.txt").write_bytes((prompt + "\n").encode("utf-8"))

    prepared = {
        "kind": kind,
        "package": {
            "id": package.meta.id,
            "version": package.meta.version,
            "content_hash": content_hash(package.root),
        },
        "agent_config": package.meta.agent_config,
        "prompt": prompt,
        "runs": runs,
        "created_at": _now(),
    }
    if subject_hash:
        prepared["subject_hash"] = subject_hash
    if kind == "agent-alone":
        prepared["agent_input_hash"] = agent_input_hash(package)
    write_json(dest / "prepared.json", prepared)
    return dest


def prepare_agent_alone(package: Package, dest: Path, runs: int = AGENT_ALONE_RUNS) -> Path:
    """Make the workspaces for V7: exactly what a candidate gets."""
    return _prepare(package, dest, kind="agent-alone", runs=runs, prompt=AGENT_ALONE_PROMPT, reference=False, brief=True)


def prepare_audit(package: Package, dest: Path, runs: int = AUDIT_RUNS) -> Path:
    """Make the workspaces for V8: the reference state, without the brief."""
    return _prepare(package, dest, kind="audit", runs=runs, prompt=AUDIT_PROMPT, reference=True, brief=False)


def _read_prepared(dest: Path, kind: str, package: Package) -> dict:
    path = Path(dest) / "prepared.json"
    command = "agent-alone" if kind == "agent-alone" else "audit"
    if not path.is_file():
        raise AgentCheckError(f"{dest} has no prepared.json. Make the runs with `gassess {command} prepare`")
    try:
        prepared = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AgentCheckError(f"{path} cannot be read: {exc}") from exc
    if prepared.get("kind") != kind:
        raise AgentCheckError(f"{dest} holds '{prepared.get('kind')}' runs, not '{kind}' runs")
    if prepared["package"]["id"] != package.meta.id:
        raise AgentCheckError(f"{dest} was prepared for package {prepared['package']['id']}, not {package.meta.id}")
    if kind == "agent-alone" and prepared["agent_input_hash"] != agent_input_hash(package):
        raise AgentCheckError(
            "the public part of the package, or its environment, session or agent settings, changed after these runs "
            "were prepared, so the agent did not see what a candidate gets. Prepare them again"
        )
    if kind == "audit" and prepared["subject_hash"] != _current_reference_hash(package):
        raise AgentCheckError("the repo or a reference patch changed after these runs were prepared. Prepare them again")
    return prepared


def _run_dirs(dest: Path) -> list[Path]:
    runs = [p for p in Path(dest).glob("run-*") if p.is_dir() and re.fullmatch(r"run-\d+", p.name)]
    if not runs:
        raise AgentCheckError(f"{dest} holds no run-N folders")
    return sorted(runs, key=lambda p: int(p.name[4:]))


def _load_run_meta(run: Path, package: Package) -> tuple[RunMeta | None, list[str]]:
    path = run / "run.yaml"
    if not path.is_file():
        return None, ["run.yaml is missing"]
    try:
        meta = RunMeta.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except yaml.YAMLError as exc:
        return None, [f"run.yaml is not valid YAML: {exc}"]
    except ValidationError as exc:
        return None, [
            f"run.yaml: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]

    problems = []
    if meta.agent_config != package.meta.agent_config:
        problems.append(f"run.yaml: agent_config is {meta.agent_config}, but the package pins {package.meta.agent_config}")
    if meta.ended_by is None:
        problems.append("run.yaml: ended_by is empty. Record how the run ended: agent_stopped, time_limit or token_limit")
    elif meta.ended_by == "error":
        problems.append("the run ended with an error, so it says nothing about the package. Run it again in a fresh workspace")
    return meta, problems


def _meta_fields(meta: RunMeta | None) -> dict:
    if meta is None:
        return {}
    return {
        "agent_config": meta.agent_config,
        "model": meta.model,
        "ended_by": meta.ended_by,
        "duration_minutes": meta.duration_minutes,
        "input_tokens": meta.input_tokens,
        "output_tokens": meta.output_tokens,
        "cost_usd": meta.cost_usd,
    }


# --- V7: agent alone ----------------------------------------------------------


def _replaced_lines(before: str, after: str) -> set[int]:
    """Line numbers (from 1) of `before` that the run changed or deleted."""
    changed: set[int] = set()
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    for tag, start, end, _, _ in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            changed.update(range(start + 1, end + 1))
    return changed


def _block_lines(text: str, symbol: str) -> set[int]:
    """Line numbers of the function or class called `symbol`, or none when it is not found."""
    lines = text.splitlines()
    pattern = re.compile(rf"^(\s*)(?:async\s+def|def|class)\s+{re.escape(symbol)}\b")
    for index, line in enumerate(lines):
        found = pattern.match(line)
        if not found:
            continue
        indent = len(found.group(1))
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
            end += 1
        return set(range(index + 1, end + 1))
    return set()


def _evidence_changed(package: Package, repo: Path, entry: str) -> bool:
    """Did the run change the place a truth_evidence entry points at?

    `path#L10` is that line. `path::name` is that function or class. A bare path is the whole file.
    """
    location, _, symbol = entry.partition("::")
    name, _, line = location.partition("#L")
    before_path, after_path = package.repo_dir / name, repo / name
    if not before_path.is_file():
        return False
    if not after_path.is_file():
        return True
    before = before_path.read_bytes().decode("utf-8", errors="replace")
    after = after_path.read_bytes().decode("utf-8", errors="replace")
    if before == after:
        return False
    changed = _replaced_lines(before, after)
    if line.isdigit():
        return int(line) in changed
    if symbol:
        block = _block_lines(before, symbol)
        return bool(block & changed) if block else True
    return True


def _touches_truth_side(package: Package, item: ChecklistItem, repo: Path) -> bool:
    """True when the run edited the places that confirm the intended truth.

    Only a hint that the agent may have changed the side that was already right.
    """
    return any(_evidence_changed(package, repo, entry) for entry in item.truth_evidence)


def _changed_files(package: Package, repo: Path) -> list[str]:
    before, after = tree_files(package.repo_dir), tree_files(repo)
    return sorted(
        name
        for name in set(before) | set(after)
        if name not in before or name not in after or file_sha256(before[name]) != file_sha256(after[name])
    )


def _tree_digest(repo: Path) -> str:
    entries = sorted(f"{name}\0{file_sha256(path)}\n" for name, path in tree_files(repo).items())
    return "sha256:" + hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()


def _summarise_run(package: Package, repo: Path, graded: WorkspaceGrade) -> dict:
    found = identified(graded.matches)
    items: dict[str, dict] = {}
    for item in package.checklist:
        if item.grading == "test":
            fixed = graded.result.items[item.id].fixed
        elif graded.rubric is not None:
            fixed = graded.rubric.items.get(item.id, "needs_review")
        else:
            fixed = "not_graded"
        record: dict = {"fixed": fixed}
        if item.kind == "gremlin":
            record["identified"] = item.id in found
            record["touched_truth_side"] = item.contradiction and _touches_truth_side(package, item, repo)
        items[item.id] = record

    outcomes = [match.outcome for match in graded.matches]
    return {
        "integrity_ok": graded.result.integrity_ok,
        "flags": [flag.describe() for flag in graded.result.flags],
        "changed_files": len(_changed_files(package, repo)),
        "tree_sha256": _tree_digest(repo),
        "items": items,
        "findings": {
            "total": len(graded.findings),
            "false_positives": outcomes.count("false_positive"),
            "extras": outcomes.count("extra"),
            "needs_review": outcomes.count("needs_review"),
            "problems": graded.findings_problems,
        },
        "grader": graded.usage,
    }


def _grade_run(package: Package, run: Path, **grading) -> dict:
    meta, problems = _load_run_meta(run, package)
    entry: dict = {"id": run.name, **_meta_fields(meta)}
    workspace = run / "workspace"
    repo = workspace / package.meta.candidate_name
    if not repo.is_dir():
        problems.append(f"the repo folder {package.meta.candidate_name}/ is missing from workspace/")
    entry["problems"] = problems
    if problems:
        return entry  # a run with problems is not graded, because it cannot be counted
    graded = grade_workspace(package, workspace, run / "grading", **grading)
    entry.update(_summarise_run(package, repo, graded))
    return entry


def grade_agent_alone(
    package: Package,
    dest: Path,
    *,
    mode: Literal["container", "local"],
    runner: Runner | None = None,
    python: str | None = None,
    client: ModelClient | None = None,
    expected_tests: list[str] | None = None,
) -> dict:
    """Grade every run in `dest` and return the table that goes in validation/agent_alone.json.

    `mode` records where the hidden tests ran. Only a container run counts for V7.
    """
    prepared = _read_prepared(dest, "agent-alone", package)
    entries = [
        _grade_run(package, run, runner=runner, python=python, client=client, expected_tests=expected_tests)
        for run in _run_dirs(dest)
    ]
    models = sorted({m for entry in entries for m in (entry.get("grader") or {}).get("models", [])})
    return {
        # The version these runs were graded against. The agent's input hash is the one from when they were made.
        "package": {"id": package.meta.id, "version": package.meta.version, "content_hash": content_hash(package.root)},
        "agent_input_hash": prepared["agent_input_hash"],
        "agent_config": prepared["agent_config"],
        "prompt": prepared["prompt"],
        "grading": {"mode": mode, "model": models or None},
        "runs": entries,
        "generated_at": _now(),
    }


def write_agent_alone(package: Package, table: dict) -> Path:
    package.validation_dir.mkdir(exist_ok=True)
    path = package.validation_dir / "agent_alone.json"
    write_json(path, table)
    return path


def _load_table(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("it is not a JSON object")
        return data, None
    except (OSError, ValueError) as exc:
        return None, f"validation/{path.name} cannot be read: {exc}"


def evaluate_agent_alone(package: Package, acknowledgements: Acknowledgements) -> Evaluation:
    """Decide V7 from the recorded runs. See docs/specs/06."""
    path = package.validation_dir / "agent_alone.json"
    if not path.is_file():
        return Evaluation("not_run", ["no agent-alone runs are recorded yet (see docs/specs/12, 'Validating a package')"])
    data, error = _load_table(path)
    if data is None:
        return Evaluation("not_run", [error or "unreadable"])
    try:
        return _evaluate_agent_alone(package, acknowledgements, data)
    except (KeyError, TypeError, AttributeError) as exc:
        return Evaluation(
            "not_run", [f"validation/agent_alone.json is not in the expected shape ({exc!r}). Grade the runs again"]
        )


def _evaluate_agent_alone(package: Package, acknowledgements: Acknowledgements, data: dict) -> Evaluation:
    if data["agent_input_hash"] != agent_input_hash(package):
        return Evaluation(
            "not_run",
            [
                "the public part of the package, or its environment, session or agent settings, changed after these runs "
                "were made, so the agent did not see what a candidate gets. Prepare the runs again"
            ],
        )
    if data["package"]["content_hash"] != content_hash(package.root):
        return Evaluation(
            "not_run",
            [
                "the package changed after these runs were graded, though not in anything the agent saw. "
                "Grade the same runs again with `gassess agent-alone grade`. No new agent runs are needed"
            ],
        )

    blockers: list[str] = []
    if data["grading"]["mode"] != "container":
        blockers.append("the runs were graded outside a container, which does not count. Grade them again with --image")
    if data["agent_config"] != package.meta.agent_config:
        blockers.append(f"the runs used agent config {data['agent_config']}, but the package pins {package.meta.agent_config}")
    runs = data["runs"]
    if len(runs) != AGENT_ALONE_RUNS:
        blockers.append(f"{len(runs)} runs are recorded and the check needs exactly {AGENT_ALONE_RUNS}")

    settled = {(s.run, s.item): s.fixed for s in acknowledgements.agent_alone_settled}
    gremlins = package.gremlins()
    decided: list[dict[str, str]] = []
    full_solves = 0
    for run in runs:
        if run["problems"]:
            blockers.extend(f"{run['id']}: {problem}" for problem in run["problems"])
            continue
        values = {item_id: settled.get((run["id"], item_id), record["fixed"]) for item_id, record in run["items"].items()}
        decided.append(values)
        outcomes = [values[g.id] for g in gremlins]
        if "no" in outcomes:
            continue
        if all(outcome == "yes" for outcome in outcomes):
            full_solves += 1
            continue
        unresolved = ", ".join(g.id for g in gremlins if values[g.id] != "yes")
        blockers.append(
            f"{run['id']}: {unresolved} could not be graded, and no other gremlin is unfixed. "
            "Grade the runs with --model, or settle each one in acknowledgements.yaml (agent_alone_settled)"
        )
    if blockers:
        return Evaluation("not_run", blockers)

    total = len(runs)
    per_item: dict[str, dict[str, int]] = {}
    for item in package.checklist:
        fixed = flagged = misled = 0
        for run, values in zip(runs, decided):
            record = run["items"][item.id]
            is_fixed = values[item.id] == "yes"
            fixed += is_fixed
            flagged += bool(record.get("identified")) and not is_fixed
            misled += bool(record.get("touched_truth_side")) and not is_fixed
        per_item[item.id] = {"runs": total, "fixed": fixed, "flagged_not_fixed": flagged, "possibly_misled": misled}

    warnings: list[tuple[str, str]] = []
    for item in gremlins:
        stats = per_item[item.id]
        if stats["fixed"] == total:
            warnings.append(
                (
                    f"V7:{item.id}:fixed_in_all_runs",
                    f"{item.id} was fixed in all {total} runs, so it carries no signal. Replace it or record why it stays",
                )
            )
        if item.contradiction and stats["possibly_misled"] == total:
            warnings.append(
                (
                    f"V7:{item.id}:misled_in_all_runs",
                    f"{item.id} possibly misled the agent in all {total} runs, because every run changed the side that "
                    "was already right. Note it for the reviewers",
                )
            )

    costs = [run.get("cost_usd") for run in runs if run.get("cost_usd") is not None]
    table = {
        "runs": total,
        "full_solves": full_solves,
        "limit": MAX_FULL_SOLVES,
        "per_item": per_item,
        "run_summary": [
            {
                "id": run["id"],
                "ended_by": run.get("ended_by"),
                "model": run.get("model"),
                "input_tokens": run.get("input_tokens"),
                "output_tokens": run.get("output_tokens"),
                "cost_usd": run.get("cost_usd"),
                "false_positives": run["findings"]["false_positives"],
                "changed_files": run["changed_files"],
            }
            for run in runs
        ],
        "cost_usd_total": round(sum(costs), 4) if costs else None,
    }
    if full_solves > MAX_FULL_SOLVES:
        problem = (
            f"{full_solves} of {total} runs fixed every scored gremlin, and at most {MAX_FULL_SOLVES} is allowed. "
            "Make the package harder, or replace the items the agent always solves"
        )
        return Evaluation("fail", [problem], warnings, table)
    return Evaluation("pass", [], warnings, table)


# --- V8: unlisted-flaw audit --------------------------------------------------


def _repo_files(package: Package) -> set[str]:
    """Every path the repo has in the flawed or the reference state."""
    files = set(tree_files(package.repo_dir))
    for patch in package.reference_dir.glob("*.patch"):
        files.update(re.findall(r"^\+\+\+ b/(.+)$", patch.read_bytes().decode("utf-8", errors="replace"), re.M))
    return files


def _repo_path(path: str, package: Package, files: set[str]) -> str:
    """Turn a path written relative to the workspace into one relative to the repo.

    A workspace path starts with the repo folder's name. But the repo's own top-level
    package often has that name too, so 'tallybook/ledger.py' can already be repo-relative.
    The path is only stripped when the stripped form is the one that exists.
    """
    prefix = package.meta.candidate_name + "/"
    if path.startswith(prefix) and path not in files and path[len(prefix) :] in files:
        return path[len(prefix) :]
    return path


def _cluster(raw: list[dict]) -> list[dict]:
    """Merge findings from different runs that point at the same place."""
    ordered = sorted(raw, key=lambda r: (r["path"], -1 if r["start"] is None else r["start"], r["run"]))
    clusters: list[dict] = []
    for entry in ordered:
        home = None
        for cluster in clusters:
            if cluster["path"] != entry["path"] or (cluster["start"] is None) != (entry["start"] is None):
                continue
            if entry["start"] is None or gap((cluster["start"], cluster["end"]), (entry["start"], entry["end"])) <= MAX_DISTANCE:
                home = cluster
                break
        if home is None:
            home = {
                "path": entry["path"],
                "start": entry["start"],
                "end": entry["end"],
                "categories": [],
                "titles": [],
                "evidence": [],
                "runs": [],
            }
            clusters.append(home)
        elif entry["start"] is not None:
            home["start"], home["end"] = min(home["start"], entry["start"]), max(home["end"], entry["end"])
        for key, value in (
            ("categories", entry["category"]),
            ("titles", entry["title"]),
            ("evidence", entry["evidence"]),
            ("runs", entry["run"]),
        ):
            if value not in home[key]:
                home[key].append(value)
    for number, cluster in enumerate(clusters, start=1):
        cluster["id"] = f"U-{number:03d}"
    return clusters


def _span(report: dict) -> tuple[int, int] | None:
    return None if report["start"] is None else (report["start"], report["end"])


def _where(report: dict) -> str:
    return report["path"] + (f":{report['start']}" if report["start"] is not None else "")


def _known_extra(package: Package, report: dict) -> str | None:
    span = _span(report)
    if span is None:
        return None  # without a line it cannot be told which known issue is meant
    for extra in package.extras:
        if extra.location.path == report["path"] and gap(span, extra.location.line_range()) <= MAX_DISTANCE:
            return extra.id
    return None


def _near_items(package: Package, report: dict) -> list[str]:
    span = _span(report)
    near = []
    for item in package.gremlins():
        for location in item.evidence_location:
            if location.path == report["path"] and (span is None or gap(span, location.line_range()) <= MAX_DISTANCE):
                near.append(item.id)
                break
    return near


def _triage_for(report: dict, entries: list[TriageEntry], package: Package, files: set[str]) -> TriageEntry | None:
    span = _span(report)
    for entry in entries:
        path, start, end = parse_location(entry.location)  # a valid entry always parses, with a line
        if _repo_path(path, package, files) != report["path"]:
            continue
        if span is None or gap(span, (start, end)) <= MAX_DISTANCE:
            return entry
    return None


def collect_audit(package: Package, dest: Path) -> dict:
    """Read the findings of every audit run and return the table for validation/audit.json."""
    prepared = _read_prepared(dest, "audit", package)
    files = _repo_files(package)
    runs: list[dict] = []
    raw: list[dict] = []
    for run in _run_dirs(dest):
        meta, problems = _load_run_meta(run, package)
        findings_path = run / "workspace" / "FINDINGS.yaml"
        if findings_path.is_file():
            findings, finding_problems = check_findings_text(findings_path.read_text(encoding="utf-8"))
        else:
            findings, finding_problems = [], ["workspace/FINDINGS.yaml is missing"]
        runs.append({"id": run.name, **_meta_fields(meta), "findings": len(findings), "problems": problems + finding_problems})
        for finding in findings:
            raw.append(
                {
                    "run": int(run.name[4:]),
                    "path": _repo_path(finding.path, package, files),
                    "start": finding.start_line,
                    "end": finding.end_line,
                    "category": finding.category,
                    "title": finding.title,
                    "evidence": finding.evidence,
                }
            )
    reports = _cluster(raw)
    for report in reports:
        report["near_items"] = _near_items(package, report)
    return {
        "package": prepared["package"],
        "subject_hash": prepared["subject_hash"],
        "agent_config": prepared["agent_config"],
        "prompt": prepared["prompt"],
        "runs": runs,
        "reports": reports,
        "generated_at": _now(),
    }


def write_audit(package: Package, table: dict) -> Path:
    package.validation_dir.mkdir(exist_ok=True)
    path = package.validation_dir / "audit.json"
    write_json(path, table)
    return path


def evaluate_audit(package: Package, acknowledgements: Acknowledgements) -> Evaluation:
    """Decide V8 from the recorded audit and the engineer's triage. See docs/specs/06."""
    path = package.validation_dir / "audit.json"
    if not path.is_file():
        return Evaluation("not_run", ["no audit is recorded yet (see docs/specs/12, 'Validating a package')"])
    data, error = _load_table(path)
    if data is None:
        return Evaluation("not_run", [error or "unreadable"])
    try:
        return _evaluate_audit(package, acknowledgements, data)
    except (KeyError, TypeError, AttributeError) as exc:
        return Evaluation("not_run", [f"validation/audit.json is not in the expected shape ({exc!r}). Collect the audit again"])


def _evaluate_audit(package: Package, acknowledgements: Acknowledgements, data: dict) -> Evaluation:
    try:
        current = _current_reference_hash(package)
    except AgentCheckError as exc:
        return Evaluation("not_run", [str(exc)])
    if data["subject_hash"] != current:
        return Evaluation(
            "not_run",
            ["the repo or a reference patch changed after this audit ran, so it says nothing about this version. Run the audit again"],
        )

    blockers: list[str] = []
    if data["agent_config"] != package.meta.agent_config:
        blockers.append(f"the audit used agent config {data['agent_config']}, but the package pins {package.meta.agent_config}")
    runs = data["runs"]
    if len(runs) != AUDIT_RUNS:
        blockers.append(f"{len(runs)} runs are recorded and the check needs exactly {AUDIT_RUNS}")
    for run in runs:
        blockers.extend(f"{run['id']}: {problem}" for problem in run["problems"])
    if blockers:
        return Evaluation("not_run", blockers)

    problems: list[str] = []
    rows: list[dict] = []
    files = _repo_files(package)
    for report in data["reports"]:
        extra = _known_extra(package, report)
        entry = _triage_for(report, acknowledgements.audit_triage, package, files)
        label = f"{report['id']} {_where(report)} '{report['titles'][0]}'"
        if entry is None and extra is None:
            problems.append(
                f"{label} is not triaged. Fix it in the base and run the audit again, add it to the checklist and run the "
                "audit again, add it to extras.yaml, or reject it with a reason in acknowledgements.yaml (audit_triage)"
            )
            decision = None
        elif entry is None:
            decision = f"extra (already in extras.yaml as {extra})"
        else:
            decision = entry.decision
            if entry.decision == "extra" and extra is None:
                problems.append(f"{label} is triaged as an extra, but extras.yaml has no entry near it")
        rows.append(
            {
                "id": report["id"],
                "where": _where(report),
                "title": report["titles"][0],
                "runs": report["runs"],
                "decision": decision,
            }
        )
    table = {"runs": len(runs), "reports": rows, "untriaged": sum(1 for row in rows if row["decision"] is None)}
    return Evaluation("fail" if problems else "pass", problems, [], table)
