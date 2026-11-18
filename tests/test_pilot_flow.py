"""A whole pilot, end to end: real hidden tests, scripted model, reviewers and the Gate 1a numbers."""

import csv
import json
import re

import pytest

from conftest import TOY
from gremlin_assess.analysis import analyze_pilot
from gremlin_assess.cli import main
from gremlin_assess.packet import build_packet
from gremlin_assess.pipeline import grade_session
from gremlin_assess.session import load_meta, save_meta, write_walkthrough
from pilot_helpers import FULL, ZERO, finding, review_forms, run_session, scripted

PACKAGES = TOY.parent

S1_FINDINGS = [
    finding("Limit check is off by one", "bug", "tallybook/limits.py:11"),
    finding("Discount above 100 percent accepted", "bug", "tallybook/pricing.py:11-12"),
    finding("README is wrong about balance", "doc_error", "README.md:19"),
    finding("Empty descriptions are accepted", "bug", "tallybook/tallybook/ledger.py:20", status="found_not_fixed"),
    finding("The limit page is vague", "bug", "docs/limits.md:1", status="found_not_fixed"),
    finding("Percent test never fails", "test_error", "tests/test_pricing.py:12", status="found_not_fixed"),
]
S1_PATCHES = ["G-001", "G-003", "G-002", "F-001"]


def read(path):
    return path.read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def graded(toy, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("flow")
    pilot = tmp / "pilot"
    run_session(toy, pilot, tmp, "S-001", "C-01", S1_PATCHES, S1_FINDINGS, scripted(FULL, open_findings=True))
    return pilot


def test_the_report_for_a_graded_session(toy, graded):
    directory = graded / "sessions" / "S-001"
    report = read(directory / "report.md")

    assert report.startswith("# Session report S-001")
    assert "| Package | pkg-toy version 0.1.0 (sha256:" in report
    assert "| Model for grading | fake-model |" in report
    assert "**Assurance.** Manual pilot. Process partly observed. The candidate's own browser was not monitored." in report
    assert "**Identity.** Identity not verified. Authorship checked by live walkthrough: not done." in report
    assert "- Bugs: 2 of 2 fixed\n- Documentation errors: 1 of 1 fixed\n- Test errors: 0 of 1 fixed" in report

    rows = {m[0]: m for m in re.findall(r"^\| ([GFA]-\d{3}) \| (.*?) \| (.*?) \| (.*?) \|$", report, re.M)}
    assert {k: v[2] for k, v in rows.items()} == {
        "G-001": "fixed and reported",
        "G-002": "fixed and reported",
        "G-003": "fixed and reported",
        "G-004": "reported, not fixed",
        "F-001": "done",
        "A-001": "done",
    }
    assert "FINDINGS.yaml › finding 1" in rows["G-001"][3] and "grading/grade.json › items.G-001" in rows["G-001"][3]
    assert "grading/rubric.json › criteria R-003, R-004" in rows["A-001"][3]

    assert "## False positives (shown, never subtracted)\n\n- Finding 5: The limit page is vague (bug, docs/limits.md:1)" in report
    assert "## Additional findings (not scored)\n\n- Finding 4: Empty descriptions are accepted" in report
    assert "## Regressions\n\nNone." in report and "## Integrity flags\n\nNone." in report
    assert "passes [3, 3, 3], median 3" in report and 'quote: "returns the balance as a `Decimal` in whole currency units"' in report
    assert "Candidate's agent: not recorded" in report
    assert "composite" not in report.lower() and "total score" not in report.lower()


def test_the_grading_artifacts(graded):
    directory = graded / "sessions" / "S-001"
    grade = json.loads(read(directory / "grading" / "grade.json"))
    matches = json.loads(read(directory / "grading" / "matches.json"))
    rubric = json.loads(read(directory / "grading" / "rubric.json"))

    assert grade["integrity_ok"] and grade["flags"] == [] and grade["regressions"] == []
    assert {k: v["fixed"] for k, v in grade["items"].items() if v["fixed"] != "not_graded"} == {
        "G-001": "yes", "G-003": "yes", "F-001": "yes",
    }
    assert [(m["finding_index"], m["outcome"], m["item_id"] or m["extra_id"]) for m in matches["matches"]] == [
        (1, "matched", "G-001"), (2, "matched", "G-003"), (3, "matched", "G-002"),
        (4, "extra", "X-001"), (5, "false_positive", None), (6, "matched", "G-004"),
    ]
    assert rubric["items"] == {"G-002": "yes", "G-004": "no", "A-001": "yes"}
    assert rubric["valid_passes"] == 3 and rubric["tokens"] == {"input": 300, "output": 60}
    # Every model call is counted, including the three that matched finding 5
    assert json.loads(read(directory / "grading" / "model_usage.json")) == {
        "calls": 6, "input_tokens": 600, "output_tokens": 120, "models": ["fake-model"],
    }
    assert "- Grader: 600 in, 120 out across 6 calls" in read(directory / "report.md")
    assert load_meta(directory).status == "graded"


def test_the_walkthrough_result_appears_in_the_report(toy, graded):
    write_walkthrough(graded, "S-001", "consistent", "explained both fixes unaided")
    grade_session(graded, "S-001", toy)  # regrading without a model must not keep the old rubric result

    directory = graded / "sessions" / "S-001"
    report = read(directory / "report.md")
    assert "walkthrough: consistent." in report and "Walkthrough notes: explained both fixes unaided" in report
    assert "Not graded. Run grade-session with a model." in report
    assert "## Findings that need a person to decide\n\n- Finding 5:" in report
    assert not (directory / "grading" / "rubric.json").exists()
    assert not (directory / "grading" / "model_usage.json").exists()
    assert "- Grader: no model was used" in report


def test_a_session_that_is_not_collected_cannot_be_graded(toy, tmp_path):
    from gremlin_assess.session import SessionError, new_session

    new_session(tmp_path, "S-001", toy, "C-01")
    with pytest.raises(SessionError, match="no collected repo folder"):
        grade_session(tmp_path, "S-001", toy)


def test_the_gate_report_from_three_sessions_and_two_reviewers(toy, tmp_path):
    pilot, packets = tmp_path / "pilot", tmp_path / "packets"
    s1 = [finding(f["title"], f["category"], f["location"], f["status"]) for f in S1_FINDINGS]
    sessions = [
        ("S-001", "C-01", S1_PATCHES, s1, scripted(FULL, open_findings=True), {}),
        ("S-002", "C-02", ["G-001"], [finding("Off by one", "bug", "tallybook/limits.py:11")], scripted(ZERO), {}),
        ("S-003", "C-03", [], [], scripted(ZERO), {"agent_source": "own-claude-code"}),
    ]
    truth = {
        "S-001": ({"G-001": "yes", "G-002": "yes", "G-003": "yes", "F-001": "yes", "A-001": "yes"}, {"R-001": 3, "R-003": 3, "R-004": 2}, (5, 4)),
        "S-002": ({"G-001": "yes"}, {}, (2, 2)),
        "S-003": ({}, {}, (1, 1)),
    }
    for session_id, candidate, patches, findings, model, options in sessions:
        run_session(toy, pilot, tmp_path, session_id, candidate, patches, findings, model, **options)
    for session_id, cost in (("S-001", 1.5), ("S-002", 2.5)):
        meta = load_meta(pilot / "sessions" / session_id)
        meta.cost_usd = cost
        save_meta(pilot / "sessions" / session_id, meta)

    reviews = pilot / "reviews"
    for session_id, (verdicts, rubric, overalls) in truth.items():
        for reviewer, overall in zip(("R1", "R2"), overalls):
            packet = build_packet(pilot, session_id, toy, reviewer, packets, ["Jane Candidate"])
            mine = dict(verdicts)
            if (session_id, reviewer) == ("S-002", "R2"):
                mine["G-001"] = "partly"  # the one disagreement with our hidden tests
            step1, step2 = review_forms(packet, reviewer, overall, mine, rubric)
            (reviews / f"{packet}-step1.yaml").write_bytes(step1.encode("utf-8"))
            (reviews / f"{packet}-step2.yaml").write_bytes(step2.encode("utf-8"))
    (pilot / "survey.csv").write_bytes(b"session,fair,comment\nS-001,4,ok\nS-002,5,good\n")

    text = analyze_pilot(pilot, PACKAGES)
    rows = {r[0]: r[1:] for r in (line.strip("| ").split(" | ") for line in text.splitlines() if line.startswith("| ") and "---" not in line)}

    assert "3 sessions and 6 reviewer forms" in text and "directional, not statistical proof" in text
    assert rows["Item agreement (ours against reviewers)"][:2] == ["94.4%", "18"]
    assert rows["Item agreement (ours against reviewers)"][3].startswith("met")
    assert rows["Reviewer ceiling (reviewers against each other)"][:2] == ["88.9%", "9"]
    assert rows["Closeness (item agreement minus ceiling)"][0] == "+5.6 points" and rows["Closeness (item agreement minus ceiling)"][3].startswith("met")
    assert rows["Rubric pass agreement (passes within 1 point)"][:2] == ["100.0%", "12"]
    assert rows["Grader against reviewers (share within 1 point)"][0] == "100.0%"
    assert "mean absolute difference 0.00" in rows["Grader against reviewers (share within 1 point)"][3]
    assert rows["Overall check (rank correlation, directional only)"][:2] == ["1.00", "3"]
    assert rows["Agent alone (full solves in 5 runs)"][3].startswith("not measured")
    assert rows["Cost per session (our agent only)"][:2] == ["$2.00", "2"]
    assert "1 sessions used the candidate's own agent and are left out" in rows["Cost per session (our agent only)"][3]
    assert "- toy: 1.5 hours (approval 0.25, flaw_design 0.5, hidden_tests 0.5, validation 0.25)" in text
    assert "2 answers, mean fairness 4.5 of 5." in text
    assert "| S-002 | G-001 | yes | partly | |" in text and text.count("| S-00") == 1


def test_the_command_line_runs_the_whole_workflow(toy, tmp_path, capsys):
    pilot, packages = tmp_path / "pilot", str(PACKAGES)
    assert main(["session", "new", str(pilot), "S-001", str(TOY), "--candidate", "C-01"]) == 0
    assert main(["session", "start", str(pilot), "S-001"]) == 0
    assert main(["session", "note", str(pilot), "S-001", "paused 5 minutes"]) == 0
    assert main(["session", "check", str(pilot), "S-001", "--stage", "collected"]) == 1
    assert "missing: final" in capsys.readouterr().out
    assert main(["session", "new", str(pilot), "S-001", str(TOY), "--candidate", "C-01"]) == 1
    assert "already exists" in capsys.readouterr().out

    run_session(toy, tmp_path / "p2", tmp_path, "S-001", "C-01", ["G-001"], [], scripted(ZERO))
    assert main(["session", "walkthrough", str(tmp_path / "p2"), "S-001", "--result", "not-done"]) == 0
    assert main(["session", "check", str(tmp_path / "p2"), "S-001"]) == 0
    assert main(["packet", str(tmp_path / "p2"), "S-001", str(TOY), "--reviewer", "R1", "--out", str(tmp_path / "out"), "--forbid", "Jane"]) == 0
    assert "Send step1 now. Send step2 only after the step 1 form comes back." in capsys.readouterr().out

    packet = next((tmp_path / "out").iterdir()).name
    step1, step2 = review_forms(packet, "R1", 3, {"G-001": "yes"}, {})
    (tmp_path / "s1.yaml").write_bytes(step1.encode("utf-8"))
    (tmp_path / "s2.yaml").write_bytes(step2.encode("utf-8"))
    assert main(["review-check", str(TOY), str(tmp_path / "s1.yaml")]) == 0
    assert main(["review-check", str(TOY), str(tmp_path / "s2.yaml")]) == 0
    (tmp_path / "bad.yaml").write_bytes(step2.replace("minutes: 20", "minutes: 0").encode("utf-8"))
    assert main(["review-check", str(TOY), str(tmp_path / "bad.yaml")]) == 1
    capsys.readouterr()

    assert main(["analyze", str(tmp_path / "p2"), "--packages", packages, "--out", str(tmp_path / "gate.md")]) == 0
    assert "# Gate 1a measures" in read(tmp_path / "gate.md")
    assert main(["grade-session", str(tmp_path / "p2"), "S-001", str(TOY)]) == 0
    assert "No model was used" in capsys.readouterr().out
