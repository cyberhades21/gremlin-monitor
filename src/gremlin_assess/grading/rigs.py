"""Sample ways a candidate repo could try to fake its test results.

The validator applies each one to a copy of the flawed repo and checks that
the grading harness notices. See docs/specs/06 (V6) and docs/specs/08 (T1).
These are test inputs for our own harness. They are never shipped.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

_FORCE_PASS_HOOK = '''\
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.outcome = "passed"
    report.longrepr = None
'''

_PATCH_OUTCOMES = '''\
import _pytest.reports as _reports

_original = _reports.TestReport.from_item_and_call.__func__


def _forced(cls, item, call):
    report = _original(cls, item, call)
    if report.outcome == "failed":
        report.outcome = "passed"
        report.longrepr = None
    return report


_reports.TestReport.from_item_and_call = classmethod(_forced)
'''

_REWRITE_RESULTS = '''\
import atexit
import re
import sys


def _rewrite():
    for position, argument in enumerate(sys.argv):
        path = None
        if argument == "--junitxml" and position + 1 < len(sys.argv):
            path = sys.argv[position + 1]
        elif argument.startswith("--junitxml="):
            path = argument.split("=", 1)[1]
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            text = re.sub(r"<failure.*?</failure>", "", text, flags=re.S)
            text = re.sub(r"<error.*?</error>", "", text, flags=re.S)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass


atexit.register(_rewrite)
'''

_EARLY_EXIT = "import os\n\nos._exit(0)\n"

# Runs when the first hidden test file imports the candidate's code. It rewrites
# the test files that have not been collected yet, so each test passes, but
# leaves any file holding the harness's own check tests alone.
_REWRITE_HIDDEN_TESTS = '''\
import glob
import os
import re


def _rewrite_hidden_tests():
    for path in glob.glob(os.path.join(os.getcwd(), "**", "test_*.py"), recursive=True):
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if "2 + 2" in source:
                continue
            names = re.findall(r"^def (test_\\w+)", source, flags=re.M)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("".join(f"def {name}():\\n    pass\\n\\n\\n" for name in names))
        except OSError:
            pass


_rewrite_hidden_tests()
'''


def _importable_modules(repo: Path) -> list[Path]:
    """Files that run when hidden tests import the candidate's code."""
    inits = [path for path in repo.rglob("__init__.py") if "tests" not in path.relative_to(repo).parts]
    return inits or sorted(repo.glob("*.py"))


def _write(path: Path, text: str) -> None:
    # Bytes, so the line endings are the same on every machine.
    path.write_bytes(text.encode("utf-8"))


def _append_to_modules(repo: Path, payload: str) -> None:
    for module in _importable_modules(repo):
        module.write_bytes(module.read_bytes() + b"\n\n" + payload.encode("utf-8"))


def forced_pass_config(repo: Path) -> None:
    """A conftest and config file that force every test to pass."""
    _write(repo / "conftest.py", _FORCE_PASS_HOOK)
    _write(repo / "pytest.ini", "[pytest]\npython_files = nothing_*.py\n")


def plugin_from_candidate_tree(repo: Path) -> None:
    """A plugin inside the candidate's tree, wired in every way a repo can."""
    _write(repo / "forcepass_plugin.py", _FORCE_PASS_HOOK)
    _write(repo / "conftest.py", 'pytest_plugins = ["forcepass_plugin"]\n')
    _write(repo / "setup.cfg", "[tool:pytest]\naddopts = -p forcepass_plugin\n")
    _write(repo / "sitecustomize.py", "import forcepass_plugin\n")


def patched_test_outcomes(repo: Path) -> None:
    """Code in the candidate's package that turns failures into passes in memory."""
    _append_to_modules(repo, _PATCH_OUTCOMES)


def rewritten_results_file(repo: Path) -> None:
    """Code that edits the results file after the runner has written it."""
    _append_to_modules(repo, _REWRITE_RESULTS)


def early_exit_success(repo: Path) -> None:
    """Code that ends the process with a success code before any test runs."""
    _append_to_modules(repo, _EARLY_EXIT)


def rewritten_hidden_tests(repo: Path) -> None:
    """Code that rewrites hidden test files on disk before they are collected."""
    _append_to_modules(repo, _REWRITE_HIDDEN_TESTS)


# Rigs any runner must catch.
RIGS: dict[str, Callable[[Path], None]] = {
    "forced_pass_config": forced_pass_config,
    "plugin_from_candidate_tree": plugin_from_candidate_tree,
    "patched_test_outcomes": patched_test_outcomes,
    "rewritten_results_file": rewritten_results_file,
    "early_exit_success": early_exit_success,
}

# Rigs only the container runner defends against, because the defence is a
# read-only mount rather than a check. A local run is fooled by these.
CONTAINER_RIGS: dict[str, Callable[[Path], None]] = {
    "rewritten_hidden_tests": rewritten_hidden_tests,
}

# Rigs that are stopped without leaving a flag, so a missing flag is not a failure.
NO_FLAG_EXPECTED = frozenset({"rewritten_hidden_tests"})


def rig_set(container: bool) -> dict[str, Callable[[Path], None]]:
    return {**RIGS, **CONTAINER_RIGS} if container else dict(RIGS)
