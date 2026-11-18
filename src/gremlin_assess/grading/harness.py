"""Run a package's hidden tests against a final repo.

The run executes candidate code, so the harness assumes that code may try to
fake a good result. See docs/specs/08, threat T1. Its defences:

- Hidden tests are copied outside the candidate's tree and run with a config
  file the harness writes. The candidate's own config files, conftest files and
  plugins are never loaded.
- The interpreter starts in isolated mode, so nothing in the candidate's tree
  runs before the test runner does.
- Two sentinel tests with random names must come out as one pass and one fail.
- The runner must exit with the code that matches a failing sentinel.
- The results file must be consistent with itself.
- In a container (see container_runner) the hidden tests and the candidate's
  repo are mounted read-only, so candidate code cannot rewrite tests that have
  not been collected yet.

These catch blanket tricks. Code that imitates specific hidden tests it has
never seen is the accepted residual risk. The local runner is for development
only: it cannot stop candidate code rewriting hidden test files on disk.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from gremlin_assess.grading.changes import compare_trees
from gremlin_assess.grading.junit import Case, JUnitError, parse_junit
from gremlin_assess.grading.results import Flag, GradeResult, ItemResult, TestOutcome
from gremlin_assess.package import Package, split_test_ref

PYTEST_INI = """\
[pytest]
pythonpath = ../repo
addopts = --import-mode=importlib -p no:cacheprovider --continue-on-collection-errors
junit_family = xunit1
"""

# With a sentinel that always fails, a genuine run always exits with 1.
EXPECTED_EXIT_CODE = 1
SENTINEL_DIR = "sentinels"

_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc", ".venv", "node_modules")


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int | None
    timed_out: bool


# A runner takes the working directory the harness prepared and a timeout in
# seconds. The directory holds `repo/`, `hidden_tests/` (with pytest.ini and
# the sentinels) and an empty `out/`. The runner must leave the results file at
# `out/results.xml`.
Runner = Callable[[Path, int], RunOutcome]


@dataclass(frozen=True)
class _Sentinels:
    file: str
    expected: dict[str, str]  # test name -> outcome it must have


def pytest_command(python: str, tests_dir: str, ini: str, results: str) -> list[str]:
    return [
        python,
        "-I",  # isolated: no PYTHONPATH, no user site, no current directory on the path
        "-B",
        "-m", "pytest",
        "-c", ini,
        "--rootdir", tests_dir,
        "--confcutdir", tests_dir,
        "-q",
        "--junitxml", results,
        tests_dir,
    ]


def local_runner(python: str) -> Runner:
    """Run pytest as a plain process on this machine. For development."""

    def run(work: Path, timeout: int) -> RunOutcome:
        tests_dir, results = work / "hidden_tests", work / "out" / "results.xml"
        command = pytest_command(python, str(tests_dir), str(tests_dir / "pytest.ini"), str(results))
        env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTEST_", "PYTHON"))}
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            completed = subprocess.run(command, cwd=tests_dir, env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunOutcome(exit_code=None, timed_out=True)
        return RunOutcome(exit_code=completed.returncode, timed_out=False)

    return run


def _write_sentinels(tests_dir: Path) -> _Sentinels:
    # Random names, so candidate code cannot single them out by name.
    token = secrets.token_hex(6)
    passing, failing = f"test_{secrets.token_hex(6)}", f"test_{secrets.token_hex(6)}"
    directory = tests_dir / SENTINEL_DIR
    directory.mkdir(exist_ok=True)
    source = f"def {passing}():\n    assert 2 + 2 == 4\n\n\ndef {failing}():\n    assert 2 + 2 == 5\n"
    (directory / f"test_{token}.py").write_text(source, encoding="utf-8")
    return _Sentinels(f"{SENTINEL_DIR}/test_{token}.py", {passing: "passed", failing: "failed"})


def _test_files(tests_dir: Path) -> list[str]:
    names = set()
    for pattern in ("test_*.py", "*_test.py"):
        names.update(path.relative_to(tests_dir).as_posix() for path in tests_dir.rglob(pattern))
    return sorted(names)


def _dotted(file: str) -> str:
    return file[:-3].replace("/", ".")


def _file_of(case: Case, known_files: list[str]) -> str | None:
    reported = case.file.replace("\\", "/") if case.file else None
    for known in known_files:
        dotted = _dotted(known)
        if reported == known or case.classname == dotted or case.classname.startswith(dotted + "."):
            return known
        if case.name == dotted:  # how a file that failed to import is reported
            return known
    return None


def grade(
    package: Package,
    final_repo: Path,
    *,
    python: str | None = None,
    runner: Runner | None = None,
    timeout: int = 300,
    expected_tests: Iterable[str] | None = None,
) -> GradeResult:
    """Run hidden tests against `final_repo` and return per-item results.

    `runner` decides where pytest runs. The default is a local process using
    `python`, which needs the package's dependencies and pytest. Pass a
    container runner for a real grading run.

    `expected_tests` is the list recorded from the trusted reference run. When
    given, results that are missing or unexpected count against integrity.
    """
    runner = runner or local_runner(python or sys.executable)
    flags: list[Flag] = []
    integrity_ok = True

    with tempfile.TemporaryDirectory(prefix="grade-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        tests_dir, results = work / "hidden_tests", work / "out" / "results.xml"
        shutil.copytree(final_repo, work / "repo", symlinks=True, ignore=_IGNORE)
        shutil.copytree(package.tests_dir, tests_dir, ignore=_IGNORE)
        results.parent.mkdir()
        (tests_dir / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
        sentinels = _write_sentinels(tests_dir)
        known_files = _test_files(tests_dir)

        run = runner(work, timeout)

        cases: tuple[Case, ...] = ()
        if run.timed_out:
            flags.append(Flag("timeout", f"the test run did not finish within {timeout} seconds"))
            integrity_ok = False
        elif not results.is_file():
            flags.append(Flag("missing_results", "the test run produced no results file"))
            integrity_ok = False
        else:
            try:
                parsed = parse_junit(results)
            except JUnitError as exc:
                flags.append(Flag("unreadable_results", str(exc)))
                integrity_ok = False
            else:
                cases = parsed.cases
                if not parsed.consistent:
                    flags.append(Flag("inconsistent_results", "the results file disagrees with its own totals"))
                    integrity_ok = False
        if not run.timed_out and run.exit_code != EXPECTED_EXIT_CODE:
            flags.append(
                Flag(
                    "unexpected_exit_code",
                    f"the test runner exited with {run.exit_code}, and a genuine run exits with "
                    f"{EXPECTED_EXIT_CODE}",
                )
            )
            integrity_ok = False

    # Sort every case into its file. Anything we cannot place is not ours.
    by_file: dict[str, list[Case]] = {}
    import_failed: set[str] = set()
    for case in cases:
        file = _file_of(case, known_files)
        if file is None:
            flags.append(Flag("unexpected_results", f"a result for an unknown test: {case.classname} {case.name}"))
            integrity_ok = False
            continue
        by_file.setdefault(file, []).append(case)
        if case.name == _dotted(file):
            import_failed.add(file)

    sentinel_seen = {case.name: case.outcome for case in by_file.get(sentinels.file, [])}
    if sentinel_seen != sentinels.expected:
        flags.append(Flag("sentinel_mismatch", "the harness's own check tests did not come out as they must"))
        integrity_ok = False

    hidden_files = [file for file in known_files if file != sentinels.file]
    seen_tests = sorted(
        f"{file}::{case.name}"
        for file in hidden_files
        for case in by_file.get(file, [])
        if case.name != _dotted(file)
    )
    if expected_tests is not None:
        expected = set(expected_tests)
        # Tests in a file that failed to import are absent for an honest reason.
        missing = sorted(t for t in expected - set(seen_tests) if t.split("::")[0] not in import_failed)
        unexpected = sorted(set(seen_tests) - expected)
        if missing:
            flags.append(Flag("missing_results", f"{len(missing)} expected tests have no result, first: {missing[0]}"))
            integrity_ok = False
        if unexpected:
            flags.append(
                Flag("unexpected_results", f"{len(unexpected)} results were not expected, first: {unexpected[0]}")
            )
            integrity_ok = False

    def outcomes_for(file: str, name: str | None) -> list[TestOutcome]:
        selected = []
        for case in by_file.get(file, []):
            whole_file_failed = case.name == _dotted(file)
            if name is None or whole_file_failed or case.name == name or case.name.startswith(name + "["):
                selected.append(TestOutcome(file, case.name, case.outcome, case.message))
        return selected

    items: dict[str, ItemResult] = {}
    for item in package.checklist:
        if item.grading != "test":
            items[item.id] = ItemResult(item.id, "not_graded")
            continue
        outcomes: list[TestOutcome] = []
        for ref in item.tests:
            outcomes.extend(outcomes_for(*split_test_ref(ref)))
        if not integrity_ok:
            fixed = "needs_review"
        elif not outcomes:
            flags.append(Flag("missing_results", f"no results for the tests of {item.id}"))
            fixed = "needs_review"
        elif all(outcome.outcome == "passed" for outcome in outcomes):
            fixed = "yes"
        else:
            fixed = "no"
        items[item.id] = ItemResult(item.id, fixed, outcomes)

    guards = [
        TestOutcome(file, case.name, case.outcome, case.message)
        for file in hidden_files
        if file.startswith("guards/")
        for case in by_file.get(file, [])
    ]

    flags.extend(compare_trees(package.repo_dir, final_repo, package.meta.protected_paths))
    return GradeResult(
        items=items,
        guards=guards,
        flags=flags,
        integrity_ok=integrity_ok,
        exit_code=run.exit_code,
        timed_out=run.timed_out,
        seen_tests=seen_tests,
    )
