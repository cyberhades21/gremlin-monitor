"""Grade one collected session and write its results and report. See docs/specs/05 and 12."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from gremlin_assess.grading import grade
from gremlin_assess.grading.findings_llm import resolve_with_model, without_model
from gremlin_assess.grading.harness import Runner
from gremlin_assess.grading.model_client import ModelClient, UsageCounter
from gremlin_assess.grading.rubric import grade_rubric, resolve_sources
from gremlin_assess.matching import match_findings
from gremlin_assess.package import Package
from gremlin_assess.report import write_report
from gremlin_assess.session import SessionError, load_meta, save_meta, session_path, update_tracker
from workspace_tools.findings_check import check_findings_text


def _write_json(path: Path, data: dict) -> None:
    path.write_bytes((json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def grade_session(
    root: Path,
    session_id: str,
    package: Package,
    *,
    runner: Runner | None = None,
    python: str | None = None,
    client: ModelClient | None = None,
    expected_tests: list[str] | None = None,
) -> Path:
    """Run every grading step on a collected session and return the report path.

    Without a model client, rubric items are not graded and findings that
    location could not settle are marked for a person.
    """
    directory = session_path(root, session_id)
    meta = load_meta(directory)
    if meta.package_id != package.meta.id:
        raise SessionError(f"{session_id} used package {meta.package_id}, not {package.meta.id}")
    final = directory / "final"
    repo = final / package.meta.candidate_name
    if not repo.is_dir():
        raise SessionError(f"{session_id} has no collected repo folder at {repo}")

    grading = directory / "grading"
    grading.mkdir(exist_ok=True)

    result = grade(package, repo, python=python, runner=runner, expected_tests=expected_tests)
    _write_json(grading / "grade.json", result.to_dict())

    counted = UsageCounter(client) if client else None
    findings_path = directory / "FINDINGS.yaml"
    findings, problems = check_findings_text(findings_path.read_text(encoding="utf-8")) if findings_path.is_file() else ([], ["FINDINGS.yaml is missing"])
    step_one = match_findings(findings, package.checklist, package.extras, package.meta.candidate_name)
    final_matches = (
        resolve_with_model(step_one, findings, package.checklist, counted) if counted else without_model(step_one)
    )
    _write_json(
        grading / "matches.json",
        {
            "findings": [asdict(f) for f in findings],
            "matches": [asdict(m) for m in final_matches],
            "findings_problems": problems,
        },
    )

    rubric_path = grading / "rubric.json"
    if counted and package.rubric:
        rubric = grade_rubric(package, resolve_sources(package, final), counted)
        _write_json(rubric_path, rubric.to_dict())
    elif rubric_path.exists():
        rubric_path.unlink()  # a stale result must not be reported against new work

    usage_path = grading / "model_usage.json"
    if counted:
        _write_json(usage_path, counted.to_dict())
    elif usage_path.exists():
        usage_path.unlink()

    meta.status = "graded"
    save_meta(directory, meta)
    update_tracker(root, session_id, status="graded")
    return write_report(package, directory)
