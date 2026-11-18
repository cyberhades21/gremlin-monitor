"""Grade one submitted workspace: hidden tests, findings and rubric. See docs/specs/05.

Candidate sessions and the validator's agent-alone runs both go through this,
so they are graded by exactly the same steps.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from gremlin_assess.grading.findings_llm import FinalMatch, resolve_with_model, without_model
from gremlin_assess.grading.harness import Runner, grade
from gremlin_assess.grading.model_client import ModelClient, UsageCounter
from gremlin_assess.grading.results import GradeResult
from gremlin_assess.grading.rubric import RubricResult, grade_rubric, resolve_sources
from gremlin_assess.matching import match_findings
from gremlin_assess.package import Package
from workspace_tools.findings_check import Finding, check_findings_text


@dataclass
class WorkspaceGrade:
    result: GradeResult
    findings: list[Finding]
    findings_problems: list[str]
    matches: list[FinalMatch]
    rubric: RubricResult | None
    usage: dict | None  # tokens and calls of the grading model, when one was used


def write_json(path: Path, data: dict) -> None:
    path.write_bytes((json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def grade_workspace(
    package: Package,
    workspace: Path,
    out: Path,
    *,
    findings_path: Path | None = None,
    runner: Runner | None = None,
    python: str | None = None,
    client: ModelClient | None = None,
    expected_tests: list[str] | None = None,
) -> WorkspaceGrade:
    """Run every grading step on `workspace` and write the results into `out`.

    `workspace` holds the repo folder next to FINDINGS.yaml and ARCHITECTURE.md.
    Without a model client, rubric items are not graded and findings that
    location could not settle are marked for a person.
    """
    out.mkdir(parents=True, exist_ok=True)
    repo = workspace / package.meta.candidate_name

    result = grade(package, repo, python=python, runner=runner, expected_tests=expected_tests)
    write_json(out / "grade.json", result.to_dict())

    counted = UsageCounter(client) if client else None
    findings_path = findings_path or workspace / "FINDINGS.yaml"
    if findings_path.is_file():
        findings, problems = check_findings_text(findings_path.read_text(encoding="utf-8"))
    else:
        findings, problems = [], ["FINDINGS.yaml is missing"]
    step_one = match_findings(findings, package.checklist, package.extras, package.meta.candidate_name)
    matches = resolve_with_model(step_one, findings, package.checklist, counted) if counted else without_model(step_one)
    write_json(
        out / "matches.json",
        {
            "findings": [asdict(f) for f in findings],
            "matches": [asdict(m) for m in matches],
            "findings_problems": problems,
        },
    )

    rubric: RubricResult | None = None
    rubric_path = out / "rubric.json"
    if counted and package.rubric:
        rubric = grade_rubric(package, resolve_sources(package, workspace), counted)
        write_json(rubric_path, rubric.to_dict())
    elif rubric_path.exists():
        rubric_path.unlink()  # a stale result must not be reported against new work

    usage: dict | None = None
    usage_path = out / "model_usage.json"
    if counted:
        usage = counted.to_dict()
        write_json(usage_path, usage)
    elif usage_path.exists():
        usage_path.unlink()

    return WorkspaceGrade(result, findings, problems, matches, rubric, usage)
