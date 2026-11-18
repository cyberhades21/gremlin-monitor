"""Tests for V7 (agent alone) and V8 (unlisted-flaw audit). See docs/specs/06."""

import copy
import json

import pytest
import yaml

from conftest import TOY
from fakes import README_Q, TESTS_Q, ScriptedModel, rubric_reply
from gremlin_assess.agentchecks import (
    AGENT_ALONE_PROMPT,
    AUDIT_PROMPT,
    AgentCheckError,
    collect_audit,
    evaluate_agent_alone,
    evaluate_audit,
    grade_agent_alone,
    prepare_agent_alone,
    prepare_audit,
    write_agent_alone,
    write_audit,
)
from gremlin_assess.analysis import compute_gate
from gremlin_assess.cli import main
from gremlin_assess.export import export_public
from gremlin_assess.hashing import content_hash, file_sha256
from gremlin_assess.package import load_acknowledgements, load_package
from gremlin_assess.patches import apply_patches, tree_files
from gremlin_assess.reference import all_item_ids, copy_repo, reference_hash
from gremlin_assess.validator import validate, write_report
from pilot_helpers import finding, findings_yaml

BASE_ACK = "truth_confirmed: [G-001, G-002, G-003]\n"
FIXED_ALL = {"R-001": (3, [README_Q]), "R-002": (2, [TESTS_Q]), "R-003": (0, []), "R-004": (0, [])}
README_ONLY = {"R-001": (3, [README_Q]), "R-002": (0, []), "R-003": (0, []), "R-004": (0, [])}
NOTHING = {criterion: (0, []) for criterion in ("R-001", "R-002", "R-003", "R-004")}


# --- a stand-in for the agent ---------------------------------------------------


def write_run_record(package, run, *, ended_by="agent_stopped", cost=1.0):
    meta = {
        "agent_config": package.meta.agent_config,
        "model": "claude-opus-5-5",
        "ended_by": ended_by,
        "duration_minutes": 20,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_usd": cost,
    }
    (run / "run.yaml").write_bytes(yaml.safe_dump(meta).encode("utf-8"))


def finish(package, run, patches=(), *, findings=(), edit=None, **record):
    """Pretend the agent worked in `run`: apply reference patches as its edits and write its findings."""
    repo = run / "workspace" / package.meta.candidate_name
    apply_patches(repo, [package.patch_for(item_id) for item_id in patches])
    if edit:
        edit(repo)
    (run / "workspace" / "FINDINGS.yaml").write_bytes(findings_yaml(list(findings)))
    write_run_record(package, run, **record)


def misled_by_the_docs(repo):
    """An agent that believes the code and rewrites the documentation, which was the side that was right."""
    path = repo / "docs" / "limits.md"
    lines = path.read_bytes().decode("utf-8").split("\n")
    lines[4] = "A purchase equal to the limit is allowed."
    path.write_bytes("\n".join(lines).encode("utf-8"))


@pytest.fixture(scope="module")
def graded_runs(toy, tmp_path_factory):
    """Five runs of the toy package, graded for real. Only the rubric grader is scripted.

    The mode says 'container' although the hidden tests ran locally. The container path is
    covered by the Docker tests. Here only the rules on top of it are tested.
    """
    dest = prepare_agent_alone(toy, tmp_path_factory.mktemp("agent-alone") / "runs")
    plans = [
        dict(patches=["G-001", "G-003", "G-002", "G-004"], findings=[finding("Limit off by one", "bug", "tallybook/limits.py:11")]),
        dict(patches=["G-001", "G-003"]),
        dict(patches=["G-001"]),
        dict(
            patches=[],
            edit=misled_by_the_docs,
            findings=[finding("Limit check", "bug", "tallybook/limits.py:11", status="found_not_fixed")],
        ),
        dict(patches=["G-003", "G-002"]),
    ]
    for number, plan in enumerate(plans, start=1):
        finish(toy, dest / f"run-{number}", **plan)
    replies = []
    for scores in (FIXED_ALL, NOTHING, NOTHING, NOTHING, README_ONLY):
        replies += [rubric_reply(scores)] * 3
    table = grade_agent_alone(toy, dest, mode="container", client=ScriptedModel(*replies))
    return dest, table


def evaluate(toy_copy, table, acknowledgements=""):
    """Record `table` in a copy of the toy package and decide V7."""
    package = load_package(toy_copy)
    write_agent_alone(package, table)
    (toy_copy / "validation" / "acknowledgements.yaml").write_text(BASE_ACK + acknowledgements, encoding="utf-8", newline="\n")
    accepted, problems = load_acknowledgements(toy_copy)
    assert problems == []
    return evaluate_agent_alone(package, accepted)


# --- preparing runs -------------------------------------------------------------


def test_prepare_makes_workspaces_exactly_as_a_candidate_gets_them(toy, tmp_path):
    dest = prepare_agent_alone(toy, tmp_path / "runs")

    assert sorted(p.name for p in dest.glob("run-*")) == [f"run-{n}" for n in range(1, 6)]
    assert (dest / "PROMPT.txt").read_bytes().decode("utf-8").strip() == AGENT_ALONE_PROMPT
    assert AGENT_ALONE_PROMPT == "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml."

    export_public(toy, tmp_path / "candidate")
    expected = {name: file_sha256(path) for name, path in tree_files(tmp_path / "candidate").items()}
    for run in dest.glob("run-*"):
        assert {name: file_sha256(path) for name, path in tree_files(run / "workspace").items()} == expected

    record = yaml.safe_load((dest / "run-1" / "run.yaml").read_bytes())
    assert record["agent_config"] == "ac-test" and record["ended_by"] is None
    prepared = json.loads((dest / "prepared.json").read_bytes())
    assert prepared["kind"] == "agent-alone" and prepared["package"]["content_hash"] == content_hash(TOY)


def test_prepare_refuses_a_folder_that_is_in_use(toy, tmp_path):
    dest = prepare_agent_alone(toy, tmp_path / "runs", runs=1)

    with pytest.raises(AgentCheckError, match="not empty"):
        prepare_agent_alone(toy, dest, runs=1)


def test_the_audit_runs_in_the_reference_state_without_the_brief(toy, tmp_path):
    dest = prepare_audit(toy, tmp_path / "audit")

    assert len(list(dest.glob("run-*"))) == 3
    assert (dest / "PROMPT.txt").read_bytes().decode("utf-8").strip() == AUDIT_PROMPT
    workspace = dest / "run-1" / "workspace"
    assert not (workspace / "BRIEF.md").exists() and (workspace / "FINDINGS.yaml").is_file()

    reference = copy_repo(toy, tmp_path / "reference", all_item_ids(toy))
    assert {n: file_sha256(p) for n, p in tree_files(workspace / "tallybook").items()} == {
        n: file_sha256(p) for n, p in tree_files(reference).items()
    }
    assert json.loads((dest / "prepared.json").read_bytes())["subject_hash"] == reference_hash(toy)


# --- V7 -------------------------------------------------------------------------


def test_the_table_records_what_each_run_did(graded_runs):
    _, table = graded_runs
    one, two, _three, four, five = table["runs"]

    assert table["grading"] == {"mode": "container", "model": ["fake-model"]}
    assert one["items"]["G-001"] == {"fixed": "yes", "identified": True, "touched_truth_side": False}
    assert one["items"]["G-002"]["fixed"] == "yes" and one["items"]["G-004"]["fixed"] == "yes"
    assert two["items"]["G-002"]["fixed"] == "no"
    assert four["items"]["G-001"] == {"fixed": "no", "identified": True, "touched_truth_side": True}
    # The README fix changes line 19. G-001's evidence in the README is line 17.
    assert five["items"]["G-001"]["touched_truth_side"] is False
    assert (one["input_tokens"], one["cost_usd"], one["ended_by"]) == (1000, 1.0, "agent_stopped")
    assert one["grader"]["calls"] == 3
    assert (one["changed_files"], four["changed_files"]) == (4, 1)
    assert one["findings"]["false_positives"] == 0
    assert all(run["tree_sha256"].startswith("sha256:") and run["integrity_ok"] for run in table["runs"])


def test_one_full_solve_passes_and_every_item_is_counted(toy_copy, graded_runs):
    evaluation = evaluate(toy_copy, graded_runs[1])

    assert (evaluation.status, evaluation.problems, evaluation.warnings) == ("pass", [], [])
    table = evaluation.table
    assert (table["runs"], table["full_solves"], table["cost_usd_total"]) == (5, 1, 5.0)
    assert table["per_item"]["G-001"] == {"runs": 5, "fixed": 3, "flagged_not_fixed": 1, "possibly_misled": 1}
    assert [table["per_item"][i]["fixed"] for i in ("G-002", "G-003", "G-004")] == [2, 3, 1]


def test_more_than_one_full_solve_fails(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    table["runs"][1]["items"] = copy.deepcopy(table["runs"][0]["items"])

    evaluation = evaluate(toy_copy, table)

    assert evaluation.status == "fail"
    assert evaluation.problems == [
        "2 of 5 runs fixed every scored gremlin, and at most 1 is allowed. "
        "Make the package harder, or replace the items the agent always solves"
    ]


def test_a_gremlin_fixed_in_every_run_is_a_warning(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    for run in table["runs"]:
        run["items"]["G-003"]["fixed"] = "yes"

    evaluation = evaluate(toy_copy, table)

    assert evaluation.status == "pass"
    assert evaluation.warnings == [
        ("V7:G-003:fixed_in_all_runs", "G-003 was fixed in all 5 runs, so it carries no signal. Replace it or record why it stays")
    ]


def test_a_contradiction_that_misleads_every_run_is_a_warning(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    for run in table["runs"]:
        run["items"]["G-002"].update(fixed="no", touched_truth_side=True)

    evaluation = evaluate(toy_copy, table)

    assert [warning_id for warning_id, _ in evaluation.warnings] == ["V7:G-002:misled_in_all_runs"]
    assert "possibly misled the agent in all 5 runs" in evaluation.warnings[0][1]


def test_a_warning_holds_validation_back_until_it_is_accepted_with_a_reason(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    for run in table["runs"]:
        run["items"]["G-003"]["fixed"] = "yes"
    evaluate(toy_copy, table)

    report = validate(toy_copy, checks=["V1", "V7"])
    assert report.check("V7").status == "pass"
    assert [w.id for w in report.unaccepted_warnings] == ["V7:G-003:fixed_in_all_runs"]

    accepted = "accepted_warnings:\n  - id: 'V7:G-003:fixed_in_all_runs'\n    reason: the warm-up item, kept on purpose\n"
    (toy_copy / "validation" / "acknowledgements.yaml").write_text(BASE_ACK + accepted, encoding="utf-8", newline="\n")
    report = validate(toy_copy, checks=["V1", "V7"])
    assert report.unaccepted_warnings == []
    assert report.check("V7").warnings[0].accepted == "the warm-up item, kept on purpose"


def test_a_change_to_what_the_agent_saw_means_running_the_agent_again(toy_copy, graded_runs):
    write_agent_alone(load_package(toy_copy), graded_runs[1])
    (toy_copy / "public" / "repo" / "README.md").write_bytes(b"changed\n")
    package = load_package(toy_copy)

    evaluation = evaluate_agent_alone(package, load_acknowledgements(toy_copy)[0])

    assert evaluation.status == "not_run"
    assert evaluation.problems[0].startswith("the public part of the package, or its environment, session or agent settings, changed")
    assert evaluation.problems[0].endswith("Prepare the runs again")
    with pytest.raises(AgentCheckError, match="changed after these runs were prepared"):
        grade_agent_alone(package, graded_runs[0], mode="container")


def test_a_change_to_the_hidden_part_means_grading_again_not_running_again(toy_copy, graded_runs, tmp_path):
    package = load_package(toy_copy)
    dest = prepare_agent_alone(package, tmp_path / "runs", runs=1)
    finish(package, dest / "run-1", ["G-001"])
    write_agent_alone(package, graded_runs[1])
    add_extra(toy_copy)  # hidden/extras.yaml: part of the version, but not something the agent saw
    package = load_package(toy_copy)

    stale = evaluate_agent_alone(package, load_acknowledgements(toy_copy)[0])
    assert stale.status == "not_run"
    assert stale.problems == [
        "the package changed after these runs were graded, though not in anything the agent saw. "
        "Grade the same runs again with `gassess agent-alone grade`. No new agent runs are needed"
    ]

    table = grade_agent_alone(package, dest, mode="container")  # the runs made before the change are still accepted
    assert table["package"]["content_hash"] == content_hash(toy_copy)
    assert table["agent_input_hash"] == json.loads((dest / "prepared.json").read_bytes())["agent_input_hash"]


def test_runs_graded_outside_a_container_do_not_count(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    table["grading"]["mode"] = "local"

    evaluation = evaluate(toy_copy, table)

    assert evaluation.status == "not_run"
    assert evaluation.problems == ["the runs were graded outside a container, which does not count. Grade them again with --image"]


def test_four_runs_are_not_enough(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    del table["runs"][4]

    assert evaluate(toy_copy, table).problems == ["4 runs are recorded and the check needs exactly 5"]


def test_a_run_with_problems_is_not_graded_and_blocks_the_check(toy_copy, tmp_path):
    package = load_package(toy_copy)
    dest = prepare_agent_alone(package, tmp_path / "runs", runs=4)
    # run-1 stays as prepared, so ended_by is empty
    (dest / "run-2" / "run.yaml").write_text("agent_config: ac-test\nended_by: error\n", encoding="utf-8")
    (dest / "run-3" / "run.yaml").write_text("agent_config: other\nended_by: agent_stopped\n", encoding="utf-8")
    (dest / "run-4" / "run.yaml").write_text("agent_config: ac-test\ncolour: red\n", encoding="utf-8")
    (dest / "run-5").mkdir()  # a run folder with nothing in it

    table = grade_agent_alone(package, dest, mode="container")

    assert [run["problems"] for run in table["runs"][:3]] == [
        ["run.yaml: ended_by is empty. Record how the run ended: agent_stopped, time_limit or token_limit"],
        ["the run ended with an error, so it says nothing about the package. Run it again in a fresh workspace"],
        ["run.yaml: agent_config is other, but the package pins ac-test"],
    ]
    assert "Extra inputs are not permitted" in table["runs"][3]["problems"][0]
    assert table["runs"][4]["problems"][0] == "run.yaml is missing"
    assert all("items" not in run for run in table["runs"])

    evaluation = evaluate(toy_copy, table)
    assert evaluation.status == "not_run"
    assert "run-2: the run ended with an error, so it says nothing about the package. Run it again in a fresh workspace" in evaluation.problems


def test_a_gremlin_that_could_not_be_graded_blocks_until_the_engineer_settles_it(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    table["runs"][1]["items"]["G-002"]["fixed"] = "not_graded"
    table["runs"][1]["items"]["G-004"]["fixed"] = "needs_review"

    blocked = evaluate(toy_copy, table)
    assert blocked.status == "not_run"
    assert blocked.problems == [
        "run-2: G-002, G-004 could not be graded, and no other gremlin is unfixed. "
        "Grade the runs with --model, or settle each one in acknowledgements.yaml (agent_alone_settled)"
    ]

    # An unquoted 'no' is read by YAML as false. The loader accepts it.
    settle = (
        "agent_alone_settled:\n"
        "  - run: run-2\n    item: G-002\n    fixed: no\n    reason: the README still says cents\n"
        "  - run: run-2\n    item: G-004\n    fixed: no\n    reason: the test still passes without an error\n"
    )
    assert evaluate(toy_copy, table, settle).status == "pass"

    both_fixed = settle.replace("fixed: no", "fixed: yes")
    assert evaluate(toy_copy, table, both_fixed).status == "fail"  # run-2 then solves everything, a second full solve


def test_a_flagged_run_cannot_be_counted(toy_copy, graded_runs):
    table = copy.deepcopy(graded_runs[1])
    for item_id in ("G-001", "G-003"):
        table["runs"][0]["items"][item_id]["fixed"] = "needs_review"  # what the harness reports after a failed integrity check

    evaluation = evaluate(toy_copy, table)

    assert evaluation.status == "not_run"
    assert evaluation.problems[0].startswith("run-1: G-001, G-003 could not be graded")


# --- V8 -------------------------------------------------------------------------


def write_findings(run, entries):
    (run / "workspace" / "FINDINGS.yaml").write_bytes(findings_yaml(entries))


@pytest.fixture
def audit(toy_copy, tmp_path):
    package = load_package(toy_copy)
    dest = prepare_audit(package, tmp_path / "audit")
    unfixed = dict(status="found_not_fixed")
    write_findings(
        dest / "run-1",
        [
            finding("Empty description accepted", "bug", "tallybook/ledger.py:20", **unfixed),
            finding("The limits page is vague", "doc_error", "docs/limits.md:3", **unfixed),
        ],
    )
    write_findings(
        dest / "run-2",
        [
            finding("add_entry skips the description check", "bug", "tallybook/tallybook/ledger.py:22", **unfixed),
            finding("README example prints nothing useful", "doc_error", "README.md:12", **unfixed),
        ],
    )
    write_findings(dest / "run-3", [])
    for number in (1, 2, 3):
        write_run_record(package, dest / f"run-{number}")
    return package, dest


def triage(toy_copy, body):
    (toy_copy / "validation" / "acknowledgements.yaml").write_text(BASE_ACK + body, encoding="utf-8", newline="\n")
    return load_acknowledgements(toy_copy)


def add_extra(toy_copy):
    """Add a known extra near README.md:12 to the registry in a copy of the toy package."""
    extras = toy_copy / "hidden" / "extras.yaml"
    extras.write_bytes(
        extras.read_bytes()
        + b'\n- id: X-002\n  summary: "README example prints nothing useful"\n'
        b'  location: {path: README.md, lines: "10-13"}\n'
        b"  found_by: audit\n  verified_on_upstream: false\n  upstream_mappable: false\n  status: open\n"
    )


def test_the_audit_merges_reports_that_point_at_the_same_place(audit):
    package, dest = audit

    table = collect_audit(package, dest)

    reports = [(r["id"], r["path"], r["start"], r["end"], r["runs"], r["near_items"]) for r in table["reports"]]
    assert reports == [
        ("U-001", "README.md", 12, 12, [2], ["G-002"]),
        ("U-002", "docs/limits.md", 3, 3, [1], []),
        ("U-003", "tallybook/ledger.py", 20, 22, [1, 2], []),
    ]
    assert table["reports"][2]["titles"] == ["Empty description accepted", "add_entry skips the description check"]
    assert table["subject_hash"] == reference_hash(package)


def test_a_report_nobody_triaged_fails_and_a_known_extra_does_not(toy_copy, audit):
    package, dest = audit
    write_audit(package, collect_audit(package, dest))

    evaluation = evaluate_audit(package, load_acknowledgements(toy_copy)[0])

    assert evaluation.status == "fail"
    # The ledger report sits inside X-001 in extras.yaml, so it needs no decision.
    assert len(evaluation.problems) == 2
    assert evaluation.problems[0].startswith("U-001 README.md:12 'README example prints nothing useful' is not triaged. Fix it in the base")
    assert evaluation.problems[1].startswith("U-002 docs/limits.md:3 'The limits page is vague' is not triaged.")
    assert evaluation.table["untriaged"] == 2
    assert evaluation.table["reports"][2]["decision"] == "extra (already in extras.yaml as X-001)"


def test_triage_covers_reports_by_location_and_the_audit_then_passes(toy_copy, audit):
    package, dest = audit
    write_audit(package, collect_audit(package, dest))
    accepted, problems = triage(
        toy_copy,
        "audit_triage:\n"
        "  - location: tallybook/README.md:14\n    decision: rejected\n    note: the example prints the balance, as intended\n"
        "  - location: docs/limits.md:8\n    decision: rejected\n    note: the page is short on purpose\n",
    )
    assert problems == []

    evaluation = evaluate_audit(package, accepted)

    assert (evaluation.status, evaluation.problems) == ("pass", [])
    assert [row["decision"] for row in evaluation.table["reports"]] == [
        "rejected",
        "rejected",
        "extra (already in extras.yaml as X-001)",
    ]


def test_an_extra_decision_needs_an_entry_in_extras_yaml(toy_copy, audit):
    package, dest = audit
    write_audit(package, collect_audit(package, dest))
    accepted, _ = triage(
        toy_copy,
        "audit_triage:\n"
        "  - location: README.md:12\n    decision: extra\n"
        "  - location: docs/limits.md:3\n    decision: rejected\n    note: the page is short on purpose\n",
    )

    evaluation = evaluate_audit(package, accepted)

    assert evaluation.status == "fail"
    assert evaluation.problems == [
        "U-001 README.md:12 'README example prints nothing useful' is triaged as an extra, but extras.yaml has no entry near it"
    ]

    add_extra(toy_copy)
    # extras.yaml is not part of the reference state, so the audit stays valid when it changes.
    assert evaluate_audit(load_package(toy_copy), accepted).status == "pass"


def test_an_audit_made_before_a_change_to_the_repo_is_not_counted(toy_copy, audit):
    package, dest = audit
    write_audit(package, collect_audit(package, dest))
    # A new file keeps every patch applying, but the reference state is no longer the one that was audited.
    (toy_copy / "public" / "repo" / "docs" / "extra.md").write_bytes(b"A new page.\n")

    evaluation = evaluate_audit(load_package(toy_copy), load_acknowledgements(toy_copy)[0])

    assert evaluation.status == "not_run"
    assert evaluation.problems[0].startswith("the repo or a reference patch changed after this audit ran")


def test_an_audit_cannot_conclude_when_a_reference_patch_no_longer_applies(toy_copy, audit, tmp_path):
    package, dest = audit
    write_audit(package, collect_audit(package, dest))
    (toy_copy / "public" / "repo" / "README.md").write_bytes(b"changed\n")  # G-002's patch no longer fits
    broken = load_package(toy_copy)

    evaluation = evaluate_audit(broken, load_acknowledgements(toy_copy)[0])
    assert evaluation.status == "not_run"
    assert evaluation.problems[0].startswith("the reference state cannot be built, so fix the reference patches first (check V4): G-002.patch")

    with pytest.raises(AgentCheckError, match="cannot be built"):
        collect_audit(broken, dest)
    with pytest.raises(AgentCheckError, match="cannot be built"):
        prepare_audit(broken, tmp_path / "again")
    assert not (tmp_path / "again").exists()  # nothing is left behind


def test_audit_runs_with_a_bad_record_or_findings_file_block_the_check(toy_copy, audit):
    package, dest = audit
    (dest / "run-1" / "workspace" / "FINDINGS.yaml").write_text("findings: 5\n", encoding="utf-8")
    (dest / "run-2" / "workspace" / "FINDINGS.yaml").unlink()
    (dest / "run-3" / "run.yaml").write_text("agent_config: ac-test\nended_by: error\n", encoding="utf-8")
    write_audit(package, collect_audit(package, dest))

    evaluation = evaluate_audit(package, load_acknowledgements(toy_copy)[0])

    assert evaluation.status == "not_run"
    assert evaluation.problems == [
        "run-1: 'findings' must be a list",
        "run-2: workspace/FINDINGS.yaml is missing",
        "run-3: the run ended with an error, so it says nothing about the package. Run it again in a fresh workspace",
    ]


def test_a_malformed_triage_entry_is_a_structure_problem(toy_copy):
    triage(
        toy_copy,
        "audit_triage:\n"
        "  - location: README.md\n    decision: rejected\n    note: not a real issue at all\n"
        "  - location: README.md:12\n    decision: rejected\n"
        "  - location: README.md:13\n    decision: fix_it\n",
    )

    problems = validate(toy_copy, checks=["V1"]).check("V1").problems

    assert "acknowledgements.yaml: audit_triage.0: location must look like path/to/file.py:42" in problems
    assert "acknowledgements.yaml: audit_triage.1: a rejected report needs a note saying why it is not a real issue" in problems
    assert any(p.startswith("acknowledgements.yaml: audit_triage.2.decision") for p in problems)


# --- the validator, Gate 1a and the command line ----------------------------------


def test_the_validator_reads_both_tables_and_saves_them(toy_copy, graded_runs, audit):
    package, dest = audit
    write_agent_alone(package, graded_runs[1])
    write_audit(package, collect_audit(package, dest))
    triage(
        toy_copy,
        "audit_triage:\n"
        "  - location: README.md:12\n    decision: rejected\n    note: the example prints the balance\n"
        "  - location: docs/limits.md:3\n    decision: rejected\n    note: the page is short on purpose\n",
    )

    report = validate(toy_copy, checks=["V1", "V7", "V8"])

    assert (report.check("V7").status, report.check("V8").status) == ("pass", "pass")
    assert report.agent_alone["full_solves"] == 1 and report.audit["untriaged"] == 0
    saved = json.loads(write_report(report, toy_copy).read_bytes())
    assert saved["agent_alone"]["runs"] == 5 and saved["audit"]["runs"] == 3


def test_gate_1a_shows_the_agent_alone_result_per_package():
    def measure(results):
        return next(m for m in compute_gate([], [], {}, agent_alone=results).measures if m.name.startswith("Agent alone"))

    assert measure(None).status == "not_measured"
    one = measure({"pkg-a": (1, 5), "pkg-b": (0, 5)})
    assert (one.status, one.shown(), one.note) == ("met", "1", "pkg-a: 1 of 5; pkg-b: 0 of 5")
    assert measure({"pkg-a": (2, 5)}).status == "not_met"


def test_command_line_for_both_checks(toy_copy, tmp_path, capsys):
    package_dir = str(toy_copy)
    runs = str(tmp_path / "runs")

    assert main(["agent-alone", "prepare", package_dir, runs, "--runs", "2"]) == 0
    assert AGENT_ALONE_PROMPT in capsys.readouterr().out

    assert main(["agent-alone", "grade", package_dir, runs]) == 0
    out = capsys.readouterr().out
    assert "run-1: not graded. run.yaml: ended_by is empty" in out
    assert "Graded outside a container" in out and "V7 would be: not_run" in out
    assert (toy_copy / "validation" / "agent_alone.json").is_file()
    assert main(["agent-alone", "grade", package_dir, str(tmp_path / "nowhere")]) == 1
    assert "has no prepared.json" in capsys.readouterr().out

    audit_dir = str(tmp_path / "audit")
    assert main(["audit", "prepare", package_dir, audit_dir]) == 0
    assert AUDIT_PROMPT in capsys.readouterr().out
    package = load_package(toy_copy)
    for number in (1, 2, 3):
        write_run_record(package, tmp_path / "audit" / f"run-{number}")
    assert main(["audit", "collect", package_dir, audit_dir]) == 0
    out = capsys.readouterr().out
    assert "The runs reported nothing." in out and "V8 would be: pass" in out


def test_validate_prints_warnings_and_says_what_to_do(toy_copy, graded_runs, capsys):
    table = copy.deepcopy(graded_runs[1])
    for run in table["runs"]:
        run["items"]["G-003"]["fixed"] = "yes"
    write_agent_alone(load_package(toy_copy), table)

    assert main(["validate", str(toy_copy), "--only", "V1", "V7"]) == 0
    out = capsys.readouterr().out
    assert "! V7:G-003:fixed_in_all_runs (not accepted yet)" in out
    assert "Not validated yet. Not run:" in out
