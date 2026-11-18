"""Grade one collected session and write its results and report. See docs/specs/05 and 12."""

from __future__ import annotations

from pathlib import Path

from gremlin_assess.grading.harness import Runner
from gremlin_assess.grading.model_client import ModelClient
from gremlin_assess.grading.workspace import grade_workspace
from gremlin_assess.package import Package
from gremlin_assess.report import write_report
from gremlin_assess.session import SessionError, load_meta, save_meta, session_path, update_tracker


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

    grade_workspace(
        package,
        final,
        directory / "grading",
        findings_path=directory / "FINDINGS.yaml",
        runner=runner,
        python=python,
        client=client,
        expected_tests=expected_tests,
    )

    meta.status = "graded"
    save_meta(directory, meta)
    update_tracker(root, session_id, status="graded")
    return write_report(package, directory)
