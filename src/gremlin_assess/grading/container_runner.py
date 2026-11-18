"""Grade inside a container. See docs/specs/08, threat T1.

The container has no network, no extra privileges and a read-only root file
system. The candidate's repo and the hidden tests are mounted read-only, so
candidate code cannot rewrite test files before they are collected. Only the
results directory is writable.
"""

from __future__ import annotations

from pathlib import Path

from gremlin_assess.container import Mount, open_permissions, run_container
from gremlin_assess.grading.harness import RunOutcome, Runner, pytest_command


def container_runner(image: str, *, memory: str = "2g", cpus: float = 1.0) -> Runner:
    def run(work: Path, timeout: int) -> RunOutcome:
        open_permissions(work)
        mounts = (
            Mount(work / "repo", "/grade/repo", read_only=True),
            Mount(work / "hidden_tests", "/grade/hidden_tests", read_only=True),
            Mount(work / "out", "/grade/out", read_only=False),
        )
        command = pytest_command(
            "python", "/grade/hidden_tests", "/grade/hidden_tests/pytest.ini", "/grade/out/results.xml"
        )
        outcome = run_container(
            image,
            command,
            mounts=mounts,
            workdir="/grade/hidden_tests",
            memory=memory,
            cpus=cpus,
            env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=timeout,
        )
        return RunOutcome(outcome.exit_code, outcome.timed_out)

    return run
