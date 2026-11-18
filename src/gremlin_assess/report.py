"""Build the per-session report. See docs/specs/05, "Report contents".

There is no composite score. Every result line points at a file in the session
folder. In the manual pilot a pointer is a file and a place in it. Times in the
terminal recording come with the platform pilot, because nothing links test
results to recording times yet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gremlin_assess.brief import CATEGORY_LABELS
from gremlin_assess.hashing import content_hash
from gremlin_assess.package import Package
from gremlin_assess.session import SessionMeta, load_meta

ASSURANCE_LINE = "Manual pilot. Process partly observed. The candidate's own browser was not monitored."
WALKTHROUGH_RESULTS = ("consistent", "doubtful", "not done")
_RESULT_LINE = re.compile(r"^Result:\s*(consistent|doubtful|not done)\s*$", re.IGNORECASE)


class ReportError(Exception):
    pass


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def walkthrough_result(session_dir: Path) -> tuple[str, str]:
    """The result and notes from walkthrough.md. A missing or unreadable file counts as not done."""
    path = session_dir / "walkthrough.md"
    if not path.is_file():
        return "not done", ""
    lines = path.read_text(encoding="utf-8").splitlines()
    match = _RESULT_LINE.match(lines[0].strip()) if lines else None
    if not match:
        return "not done", "walkthrough.md must start with 'Result: consistent', 'Result: doubtful' or 'Result: not done'"
    return match.group(1).lower(), "\n".join(lines[1:]).strip()


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def combined_results(package: Package, grade: dict, rubric: dict | None) -> dict[str, str]:
    """Each item's Fixed result: yes, no, needs_review or not_graded."""
    results = {}
    for item in package.checklist:
        if item.grading == "test":
            results[item.id] = grade["items"][item.id]["fixed"]
        else:
            results[item.id] = (rubric or {}).get("items", {}).get(item.id, "not_graded")
    return results


def identified_ids(matches: dict) -> set[str]:
    return {m["item_id"] for m in matches["matches"] if m["outcome"] in ("matched", "matched_by_grader") and m["item_id"]}


def _gremlin_label(fixed: str, identified: bool) -> str:
    if fixed in ("needs_review", "not_graded"):
        return fixed.replace("_", " ")
    if fixed == "yes":
        return "fixed and reported" if identified else "fixed, not reported"
    return "reported, not fixed" if identified else "missed"


def _task_label(fixed: str) -> str:
    return {"yes": "done", "no": "not done"}.get(fixed, fixed.replace("_", " "))


def _pointer(package: Package, item, grade: dict, rubric: dict | None) -> str:
    if item.grading == "test":
        tests = ", ".join(sorted({f"{t['file']}::{t['name']}" for t in grade["items"][item.id]["tests"]}))
        return f"grading/grade.json › items.{item.id} ({tests})"
    if rubric is None:
        return "not graded: run grade-session with a model"
    criteria = ", ".join(c.id for c in package.rubric if c.item == item.id)
    return f"grading/rubric.json › criteria {criteria}"


def render_report(package: Package, session_dir: Path) -> str:
    meta: SessionMeta = load_meta(session_dir)
    grade = read_json(session_dir / "grading" / "grade.json")
    if grade is None:
        raise ReportError("grading/grade.json is missing. Run grade-session first")
    matches = read_json(session_dir / "grading" / "matches.json") or {"findings": [], "matches": [], "findings_problems": []}
    rubric = read_json(session_dir / "grading" / "rubric.json")
    fixed = combined_results(package, grade, rubric)
    identified = identified_ids(matches)
    walk, walk_notes = walkthrough_result(session_dir)
    findings = {f["index"]: f for f in matches["findings"]}

    out = [f"# Session report {meta.session}", ""]

    # 1. Header
    out += _table(
        ["Field", "Value"],
        [
            ["Session", meta.session],
            ["Package", f"{package.meta.id} version {package.meta.version} ({content_hash(package.root)})"],
            ["Agent config", meta.agent_config],
            ["Agent source", meta.agent_source],
            ["Model for grading", ", ".join((rubric or {}).get("models", [])) or "no model pass was run"],
            ["Started", meta.start or "not recorded"],
            ["Submitted", meta.end or "not recorded"],
        ],
    )

    # 2 and 3. Assurance and identity
    out += ["", f"**Assurance.** {ASSURANCE_LINE}", "", f"**Identity.** Identity not verified. Authorship checked by live walkthrough: {walk}."]
    if walk_notes:
        out += ["", f"Walkthrough notes: {walk_notes}"]

    # 4. Per-category lines
    out += ["", "## Planted issues by category", ""]
    for category, label in CATEGORY_LABELS.items():
        own = [i for i in package.gremlins() if i.category == category]
        done = sum(1 for i in own if fixed[i.id] == "yes")
        review = sum(1 for i in own if fixed[i.id] in ("needs_review", "not_graded"))
        suffix = f" ({review} need review or are not graded)" if review else ""
        out.append(f"- {label}: {done} of {len(own)} fixed{suffix}")

    # 5. Items
    out += ["", "## Items", ""]
    rows = []
    for item in package.checklist:
        if item.kind == "gremlin":
            result = _gremlin_label(fixed[item.id], item.id in identified)
            pointer = _pointer(package, item, grade, rubric)
            if item.id in identified:
                numbers = [str(m["finding_index"]) for m in matches["matches"] if m["item_id"] == item.id]
                pointer += f"; FINDINGS.yaml › finding {', '.join(numbers)}"
        else:
            result, pointer = _task_label(fixed[item.id]), _pointer(package, item, grade, rubric)
        rows.append([item.id, item.summary, result, pointer])
    out += _table(["Item", "What", "Result", "Evidence"], rows)

    # 6. Regressions, false positives, extras, flags
    out += ["", "## Regressions", ""]
    out += [f"- {g['file']}::{g['name']} is {g['outcome']}" for g in grade["regressions"]] or ["None."]

    def finding_line(m: dict) -> str:
        f = findings.get(m["finding_index"], {})
        where = f"{f.get('path', '?')}" + (f":{f['start_line']}" if f.get("start_line") else "")
        return f"- Finding {m['finding_index']}: {f.get('title', '')} ({f.get('category', '?')}, {where})"

    sections = [
        ("False positives (shown, never subtracted)", "false_positive"),
        ("Additional findings (not scored)", "extra"),
        ("Other findings (not scored)", "other"),
        ("Findings that need a person to decide", "needs_review"),
    ]
    for title, outcome in sections:
        listed = [m for m in matches["matches"] if m["outcome"] == outcome]
        out += ["", f"## {title}", ""]
        out += [finding_line(m) for m in listed] or ["None."]
    if matches.get("findings_problems"):
        out += ["", "Problems reading FINDINGS.yaml:"] + [f"- {p}" for p in matches["findings_problems"]]

    flags = [f"{f['kind']}: {f['detail']}" + (f" ({f['path']})" if f.get("path") else "") for f in grade["flags"]]
    flags += [f"{f['kind']}: {f['detail']}" for f in (rubric or {}).get("flags", [])]
    out += ["", "## Integrity flags", ""]
    out += [f"- {flag}" for flag in flags] or ["None."]
    if not grade["integrity_ok"]:
        out += ["", "The test run could not be trusted, so every test-backed result above needs review."]

    # 7. Rubric scores with quotes
    out += ["", "## Rubric scores", ""]
    if rubric is None:
        out.append("Not graded. Run grade-session with a model.")
    else:
        for criterion in package.rubric:
            r = rubric["criteria"][criterion.id]
            state = "needs review" if r["needs_review"] else f"median {r['median']}"
            out.append(f"- {criterion.id} (item {criterion.item}): passes {r['scores']}, {state}. {criterion.criterion}")
            if r["reason"]:
                out.append(f"  - {r['reason']}")
            for quote in sorted({q for pass_quotes in r["quotes"] for q in pass_quotes}):
                out.append(f"  - quote: \"{' '.join(quote.split())}\"")

    # 8. Process
    out += ["", "## Process observations", "", "Rated by reviewers in the manual pilot. Nothing here is derived automatically yet."]

    # 9. Tokens and cost
    out += ["", "## Tokens and cost", ""]
    if meta.agent_source == "ours":
        spent = f"{meta.input_tokens} in, {meta.output_tokens} out, ${meta.cost_usd}" if meta.cost_usd is not None else "not recorded"
        out.append(f"- Candidate's agent: {spent}")
    else:
        out.append("- Candidate's agent: supplied by the candidate, so cost was not measured.")
    usage = read_json(session_dir / "grading" / "model_usage.json")
    if usage:
        out.append(f"- Grader: {usage['input_tokens']} in, {usage['output_tokens']} out across {usage['calls']} calls")
    else:
        out.append("- Grader: no model was used")
    return "\n".join(out) + "\n"


def write_report(package: Package, session_dir: Path) -> Path:
    path = session_dir / "report.md"
    path.write_bytes(render_report(package, session_dir).encode("utf-8"))
    return path
