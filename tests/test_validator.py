"""Tests for the validator, including the acceptance checks in docs/specs/06."""

import json
import shutil

import pytest

from gremlin_assess.cli import main
from gremlin_assess.patches import apply_patches, make_patch
from gremlin_assess.validator import validate, write_report

from conftest import TOY


def statuses(report):
    return {check.id: check.status for check in report.checks}


@pytest.fixture(scope="module")
def toy_report():
    return validate(TOY)


def test_toy_passes_every_check_that_exists(toy_report):
    assert statuses(toy_report) == {
        "V1": "pass",
        "V2": "not_run",
        "V3": "pass",
        "V4": "pass",
        "V5": "pass",
        "V6": "pass",
        "V7": "not_run",
        "V8": "not_run",
        "V9": "pass",
        "V10": "pass",
        "V11": "pass",
        "V12": "pass",
    }
    assert len(toy_report.expected_tests) == 9
    assert toy_report.content_hash.startswith("sha256:")


def test_a_package_is_not_validated_while_checks_are_missing(toy_report):
    assert not toy_report.validated
    assert toy_report.check("V7").problems == [
        "no agent-alone runs are recorded yet (see docs/specs/12, 'Validating a package')"
    ]
    assert toy_report.check("V8").problems == [
        "no audit is recorded yet (see docs/specs/12, 'Validating a package')"
    ]


def test_two_runs_give_the_same_results(toy_report):
    assert validate(TOY).comparable() == toy_report.comparable()


def test_a_removed_flaw_fails_v3_and_names_the_item(toy_copy):
    apply_patches(toy_copy / "public" / "repo", [toy_copy / "hidden" / "reference" / "G-001.patch"])

    check = validate(toy_copy, checks=["V1", "V3"]).check("V3")

    assert check.status == "fail"
    assert check.problems == [
        "G-001: items/test_g001.py::test_rejects_amount_equal_to_limit passes on the flawed repo, "
        "so it does not detect the flaw"
    ]


def test_a_reference_that_misses_an_item_fails_v4_and_names_it(toy_copy, tmp_path):
    # Swap the patch for G-003 for one that only rewords a docstring.
    repo, changed = toy_copy / "public" / "repo", tmp_path / "changed"
    shutil.copytree(repo, changed)
    pricing = changed / "tallybook" / "pricing.py"
    pricing.write_bytes(pricing.read_bytes().replace(b"Price adjustments.", b"Price changes."))
    (toy_copy / "hidden" / "reference" / "G-003.patch").write_bytes(make_patch(repo, changed).encode("utf-8"))

    check = validate(toy_copy, checks=["V1", "V4"]).check("V4")

    assert check.status == "fail"
    assert check.problems == [
        "G-003: not fixed by the reference solution (items/test_g003.py::test_rejects_percent_over_100 is failed)"
    ]


def test_a_patch_that_fixes_two_items_fails_v5(toy_copy, tmp_path):
    repo, changed = toy_copy / "public" / "repo", tmp_path / "changed"
    shutil.copytree(repo, changed)
    reference = toy_copy / "hidden" / "reference"
    apply_patches(changed, [reference / "G-001.patch", reference / "G-003.patch"])
    (reference / "G-001.patch").write_bytes(make_patch(repo, changed).encode("utf-8"))

    check = validate(toy_copy, checks=["V1", "V5"]).check("V5")

    assert check.status == "fail"
    assert check.problems == ["G-001: its patch changes the result of G-003 from no to yes"]


def test_a_hidden_test_that_errors_instead_of_asserting_fails_v3(toy_copy):
    test_file = toy_copy / "hidden" / "tests" / "items" / "test_g001.py"
    test_file.write_bytes(
        b"from decimal import Decimal\n\nfrom tallybook.limits import is_within_limit\n\n\n"
        b"def test_rejects_amount_equal_to_limit():\n"
        b"    if is_within_limit(Decimal('100'), Decimal('100')):\n"
        b"        raise KeyError('limit')\n"
    )

    check = validate(toy_copy, checks=["V1", "V3"]).check("V3")

    assert check.status == "fail"
    assert "must fail by assertion, but it ended as failed: KeyError" in check.problems[0]


def test_an_internal_word_in_the_public_part_fails_v11(toy_copy):
    (toy_copy / "public" / "repo" / "docs" / "notes.md").write_text("remember the gremlin list\n", encoding="utf-8")

    check = validate(toy_copy, checks=["V1", "V11"]).check("V11")

    assert check.status == "fail"
    assert check.problems == ["repo/docs/notes.md:1: contains 'gremlin'"]


def test_a_changed_licence_file_fails_v10(toy_copy):
    licence = toy_copy / "public" / "repo" / "LICENSE"
    licence.write_bytes(licence.read_bytes() + b"extra line\n")
    (toy_copy / "public" / "repo" / "NOTICE").write_bytes(b"Modified in tallybook/limits.py\n")

    check = validate(toy_copy, checks=["V1", "V10"]).check("V10")

    assert check.problems == [
        "the licence file does not match its recorded hash",
        "public/repo/NOTICE must hold exactly the standard sentence",
    ]


def test_a_missing_stage_in_the_hours_log_fails_v12(toy_copy):
    hours = toy_copy / "validation" / "hours.csv"
    lines = [line for line in hours.read_text(encoding="utf-8").splitlines() if ",approval," not in line]
    hours.write_text("\n".join(lines) + "\n", encoding="utf-8")

    check = validate(toy_copy, checks=["V12"]).check("V12")

    assert check.problems == ["validation/hours.csv has no entry for stage 'approval'"]


def test_unconfirmed_truth_evidence_fails_v9(toy_copy):
    (toy_copy / "validation" / "acknowledgements.yaml").write_text("truth_confirmed: [G-001]\n", encoding="utf-8")
    checklist = toy_copy / "hidden" / "checklist.yaml"
    checklist.write_text(
        checklist.read_text(encoding="utf-8").replace("- README.md#L17", "- README.md#L99"), encoding="utf-8"
    )

    check = validate(toy_copy, checks=["V9"]).check("V9")

    assert check.problems == [
        "G-001: truth_evidence 'README.md#L99' does not exist in the public repo",
        "G-002: the engineer has not confirmed its truth evidence in acknowledgements.yaml",
        "G-003: the engineer has not confirmed its truth evidence in acknowledgements.yaml",
    ]


def test_structure_problems_fail_v1_and_hold_back_the_graded_checks(toy_copy):
    brief = toy_copy / "public" / "BRIEF.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("| Bugs | 2 |", "| Bugs | 5 |"), encoding="utf-8")
    (toy_copy / "hidden" / "reference" / "F-001.patch").unlink()
    (toy_copy / "hidden" / "tests" / "items" / "test_g003.py").unlink()

    report = validate(toy_copy, checks=["V1", "V3"])

    assert report.check("V1").problems == [
        "G-003: hidden test file items/test_g003.py is missing",
        "F-001: reference patch hidden/reference/F-001.patch is missing",
        "BRIEF.md: counts table must contain the row '| Bugs | 2 |'",
    ]
    assert report.check("V3").status == "not_run" and report.check("V3").problems == ["fix V1 first"]


def test_a_package_that_does_not_load_fails_v1_only(toy_copy):
    (toy_copy / "package.yaml").write_text("id: [broken\n", encoding="utf-8")

    report = validate(toy_copy)

    assert report.check("V1").status == "fail"
    assert all(check.status == "not_run" for check in report.checks if check.id != "V1")


def test_the_report_is_written_as_json(toy_copy):
    path = write_report(validate(toy_copy, checks=["V1"]), toy_copy)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["package"]["id"] == "pkg-toy" and saved["validated"] is False
    assert saved["generated_at"] and saved["checks"][0] == {
        "id": "V1",
        "name": "Structure",
        "status": "pass",
        "problems": [],
        "warnings": [],
    }
    assert saved["agent_alone"] is None and saved["audit"] is None


def test_command_line(capsys):
    assert main(["counts", str(TOY)]) == 0
    assert "| Bugs | 2 |" in capsys.readouterr().out

    assert main(["validate", str(TOY), "--only", "V1", "V10"]) == 0
    output = capsys.readouterr().out
    assert "V1   Structure              pass" in output and "Not validated yet." in output

    assert main(["check-findings", str(TOY / "public" / "FINDINGS.yaml")]) == 0
    assert "holds 0 findings" in capsys.readouterr().out
