"""Tests for the grading harness. Each one runs the toy package's hidden tests for real."""

import shutil

import pytest
import yaml

from gremlin_assess.grading import grade
from gremlin_assess.grading.rigs import RIGS
from gremlin_assess.package import load_package

ALL = ("G-001", "G-002", "G-003", "G-004", "F-001")


def fixed(result):
    return {item_id: item.fixed for item_id, item in result.items.items()}


def test_flawed_repo(toy):
    result = grade(toy, toy.repo_dir)

    assert result.integrity_ok and result.flags == [] and result.regressions == []
    assert fixed(result) == {
        "G-001": "no",
        "G-002": "not_graded",
        "G-003": "no",
        "G-004": "not_graded",
        "F-001": "no",
        "A-001": "not_graded",
    }
    assert len(result.guards) == 5
    assert [test.name for test in result.items["F-001"].tests] == ["test_total_by_tag", "test_total_by_unknown_tag_is_zero"]


def test_reference_solution(toy, repo_with):
    result = grade(toy, repo_with(*ALL))

    assert result.integrity_ok and result.flags == [] and result.regressions == []
    assert [fixed(result)[item_id] for item_id in ("G-001", "G-003", "F-001")] == ["yes", "yes", "yes"]


def test_one_fix_changes_one_item(toy, repo_with):
    result = grade(toy, repo_with("G-001"))

    assert [fixed(result)[item_id] for item_id in ("G-001", "G-003", "F-001")] == ["yes", "no", "no"]


def test_broken_behaviour_is_a_regression(toy, repo_with):
    repo = repo_with(*ALL)
    ledger = repo / "tallybook" / "ledger.py"
    working = b'return sum((entry.amount for entry in self.entries), Decimal("0"))'
    assert working in ledger.read_bytes()
    ledger.write_bytes(ledger.read_bytes().replace(working, b'return Decimal("0")'))

    result = grade(toy, repo)

    assert result.integrity_ok
    assert [guard.name for guard in result.regressions] == ["test_balance_sums_entries"]


@pytest.mark.parametrize(
    "name, expected_result, run_is_trusted",
    [
        # Config and plugin files in the candidate's tree are never loaded,
        # so results are unchanged and the files are flagged.
        ("forced_pass_config", "no", True),
        ("plugin_from_candidate_tree", "no", True),
        # Code that runs inside the test process can fake results, so the
        # run is not trusted and nothing is reported as fixed.
        ("patched_test_outcomes", "needs_review", False),
        ("rewritten_results_file", "needs_review", False),
        ("early_exit_success", "needs_review", False),
    ],
)
def test_rigged_repo_is_noticed(toy, tmp_path, name, expected_result, run_is_trusted):
    repo = tmp_path / "repo"
    shutil.copytree(toy.repo_dir, repo)
    RIGS[name](repo)

    result = grade(toy, repo)

    assert result.flags
    assert result.integrity_ok is run_is_trusted
    assert fixed(result)["G-001"] == fixed(result)["G-003"] == expected_result


def test_every_rig_has_a_test():
    assert set(RIGS) == {
        "forced_pass_config",
        "plugin_from_candidate_tree",
        "patched_test_outcomes",
        "rewritten_results_file",
        "early_exit_success",
    }


def test_protected_path_edit_is_flagged(toy, repo_with):
    repo = repo_with("G-001")
    (repo / "pytest.ini").write_bytes(b"[pytest]\n")

    result = grade(toy, repo)

    assert [(flag.kind, flag.path) for flag in result.flags] == [("protected_path", "pytest.ini")]
    assert result.integrity_ok and fixed(result)["G-001"] == "yes"


def test_changed_line_endings_alone_raise_no_pattern_flags(toy, repo_with):
    repo = repo_with()
    module = repo / "tallybook" / "limits.py"
    module.write_bytes(module.read_bytes().replace(b"\n", b"\r\n"))

    assert grade(toy, repo).flags == []


def test_a_hidden_test_file_that_cannot_import_fails_only_its_own_item(toy_copy):
    tests = toy_copy / "hidden" / "tests" / "items"
    (tests / "test_f002.py").write_bytes(
        b"from tallybook.reports import summary\n\n\ndef test_summary():\n    assert summary([]) == ''\n"
    )
    checklist = toy_copy / "hidden" / "checklist.yaml"
    data = yaml.safe_load(checklist.read_text(encoding="utf-8"))
    data.append(
        {
            "id": "F-002",
            "kind": "task",
            "summary": "Add a report",
            "grading": "test",
            "tests": ["hidden/tests/items/test_f002.py"],
        }
    )
    checklist.write_text(yaml.safe_dump(data), encoding="utf-8")
    package = load_package(toy_copy)

    result = grade(package, package.repo_dir)

    assert result.integrity_ok
    assert fixed(result)["F-002"] == "no"
    assert [test.outcome for test in result.items["F-002"].tests] == ["error"]
    assert fixed(result)["G-001"] == "no" and len(result.guards) == 5


def test_expected_tests_guard_against_missing_results(toy, repo_with):
    expected = grade(toy, repo_with(*ALL)).seen_tests
    assert len(expected) == 9

    assert grade(toy, toy.repo_dir, expected_tests=expected).integrity_ok

    short = grade(toy, toy.repo_dir, expected_tests=[*expected, "items/test_g001.py::test_that_vanished"])
    assert not short.integrity_ok
    assert fixed(short)["G-001"] == "needs_review"
    assert [flag.kind for flag in short.flags] == ["missing_results"]


def test_a_run_that_hangs_times_out(toy, repo_with):
    repo = repo_with()
    init = repo / "tallybook" / "__init__.py"
    init.write_bytes(init.read_bytes() + b"\nimport time\n\ntime.sleep(60)\n")

    result = grade(toy, repo, timeout=3)

    assert result.timed_out and not result.integrity_ok
    assert result.flags[0].kind == "timeout"
    assert fixed(result)["G-001"] == "needs_review"
