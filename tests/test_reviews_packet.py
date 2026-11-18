import pytest
import yaml

from fakes import ARCH_Q1, ARCH_Q2, README_Q, ScriptedModel, match_reply, rubric_reply
from gremlin_assess.packet import PacketError, build_packet, check_packet, load_assignments
from gremlin_assess.reviews import ReviewError, load_step1, load_step2, step1_template, step2_template
from pilot_helpers import FULL, finding, run_session

FINDINGS = [
    finding("Limit check is off by one", "bug", "tallybook/limits.py:11"),
    finding("README is wrong about balance", "doc_error", "README.md:19"),
]


def step1_form(packet_id, **changes):
    form = {"reviewer": "R1", "packet": packet_id, "overall": 4, "reasons": "Fixed the main bug and checked it. Missed one test."}
    return yaml.safe_dump({**form, **changes})


def step2_form(packet_id, **changes):
    form = {
        "reviewer": "R1",
        "packet": packet_id,
        "items": [{"id": i, "verdict": v} for i, v in [("G-001", "yes"), ("G-002", "yes"), ("G-003", "no"), ("G-004", "no"), ("F-001", "partly"), ("A-001", "yes")]],
        "rubric": [{"id": c, "score": s} for c, s in [("R-001", 3), ("R-002", 0), ("R-003", 3), ("R-004", 2)]],
        "process": [
            {"id": "P1", "rating": "observed", "pointer": "00:12 in terminal.cast"},
            {"id": "P2", "rating": "not_observed", "pointer": None},
            {"id": "P3", "rating": "cannot_tell", "pointer": None},
            {"id": "P4", "rating": "observed", "pointer": "transcript line 2"},
        ],
        "minutes": 25,
    }
    return yaml.safe_dump({**form, **changes})


@pytest.fixture
def session(toy, tmp_path):
    pilot = tmp_path / "pilot"
    model = ScriptedModel(*[rubric_reply(FULL)] * 3)
    run_session(toy, pilot, tmp_path, "S-001", "C-01", ["G-001", "G-002"], FINDINGS, model)
    return pilot


def test_the_templates_are_invalid_until_filled_in(toy, tmp_path):
    (tmp_path / "s1.yaml").write_bytes(step1_template("P-0a1b2c").encode("utf-8"))
    (tmp_path / "s2.yaml").write_bytes(step2_template(toy, "P-0a1b2c").encode("utf-8"))

    with pytest.raises(ReviewError):
        load_step1(tmp_path / "s1.yaml")
    with pytest.raises(ReviewError):
        load_step2(tmp_path / "s2.yaml", toy)
    filled = (tmp_path / "s2.yaml").read_bytes().decode("utf-8").replace('reviewer: ""', "reviewer: R1").replace("minutes: 0 ", "minutes: 20 ")
    (tmp_path / "s2.yaml").write_bytes(filled.encode("utf-8"))
    form = load_step2(tmp_path / "s2.yaml", toy)
    assert [i.id for i in form.items] == [i.id for i in toy.checklist] and form.minutes == 20


def test_a_reviewer_may_type_yes_and_no_unquoted(toy, tmp_path):
    text = step2_form("P-0a1b2c").replace("verdict: 'yes'", "verdict: yes").replace("verdict: 'no'", "verdict: no")
    assert "verdict: yes" in text and "verdict: no" in text
    (tmp_path / "s2.yaml").write_bytes(text.encode("utf-8"))

    form = load_step2(tmp_path / "s2.yaml", toy)

    assert {i.id: i.verdict for i in form.items} == {
        "G-001": "yes", "G-002": "yes", "G-003": "no", "G-004": "no", "F-001": "partly", "A-001": "yes",
    }


def test_valid_forms_load(toy, tmp_path):
    (tmp_path / "s1.yaml").write_bytes(step1_form("P-0a1b2c").encode("utf-8"))
    (tmp_path / "s2.yaml").write_bytes(step2_form("P-0a1b2c").encode("utf-8"))

    assert load_step1(tmp_path / "s1.yaml").overall == 4
    form = load_step2(tmp_path / "s2.yaml", toy)
    assert {i.id: i.verdict for i in form.items}["F-001"] == "partly"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"overall": 6}, "overall"),
        ({"overall": 0}, "overall"),
        ({"reasons": "good"}, "reasons"),
        ({"packet": "P-xyz"}, "packet"),
        ({"extra": 1}, "extra"),
    ],
)
def test_step1_rules(tmp_path, changes, message):
    (tmp_path / "s1.yaml").write_bytes(step1_form("P-0a1b2c", **changes).encode("utf-8"))
    with pytest.raises(ReviewError, match=message):
        load_step1(tmp_path / "s1.yaml")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"items": [{"id": "G-001", "verdict": "yes"}]}, "items must cover exactly"),
        ({"rubric": [{"id": "R-001", "score": 4}]}, "rubric"),
        ({"minutes": 0}, "minutes"),
        ({"process": [{"id": "P1", "rating": "observed", "pointer": None}]}, "needs a pointer"),
        ({"items": [{"id": i, "verdict": "mostly"} for i in ("G-001", "G-002", "G-003", "G-004", "F-001", "A-001")]}, "verdict"),
    ],
)
def test_step2_rules(toy, tmp_path, changes, message):
    (tmp_path / "s2.yaml").write_bytes(step2_form("P-0a1b2c", **changes).encode("utf-8"))
    with pytest.raises(ReviewError, match=message):
        load_step2(tmp_path / "s2.yaml", toy)


def test_a_packet_holds_the_work_and_never_our_results(toy, session, tmp_path):
    out = tmp_path / "packets"

    packet_id = build_packet(session, "S-001", toy, "R1", out, ["Jane Candidate", "jane@example.com"])

    step1, step2 = out / packet_id / "step1", out / packet_id / "step2"
    assert sorted(p.name for p in step1.iterdir()) == [
        "ARCHITECTURE.md", "BRIEF.md", "FINDINGS.yaml", "REVIEW-step1.yaml", "final-changes.patch", "terminal.cast", "transcript",
    ]
    assert sorted(p.name for p in step2.iterdir()) == ["GREMLIN-LIST.md", "REVIEW-step2.yaml"]
    assert "GREMLIN-LIST" not in "".join(p.name for p in step1.rglob("*"))
    patch = (step1 / "final-changes.patch").read_bytes().decode("utf-8")
    assert "+++ b/tallybook/limits.py" in patch and "+++ b/README.md" in patch
    listing = (step2 / "GREMLIN-LIST.md").read_bytes().decode("utf-8")
    assert "## G-001:" in listing and "What is right: A purchase at or above the limit is rejected" in listing
    assert "## R-001 (item G-002)" in listing and "P4: Resolved a contradiction with evidence" in listing
    assert check_packet(out / packet_id, ["Jane Candidate"]) == []
    assert load_assignments(session / "reviews") == {packet_id: ("S-001", "R1")}
    assert (session / "reviews" / "assignments.csv").read_bytes().decode("utf-8").splitlines()[0] == "packet,session,reviewer"


def test_every_packet_gets_its_own_random_id(toy, session, tmp_path):
    first = build_packet(session, "S-001", toy, "R1", tmp_path / "p", ["Jane"])
    second = build_packet(session, "S-001", toy, "R2", tmp_path / "p", ["Jane"])

    assert first != second and first.startswith("P-") and len(first) == 8
    assert load_assignments(session / "reviews") == {first: ("S-001", "R1"), second: ("S-001", "R2")}


def test_a_packet_with_the_candidates_name_is_not_written(toy, session, tmp_path):
    transcript = session / "sessions" / "S-001" / "transcript" / "agent.log"
    transcript.write_bytes(b"user: hi, I am Jane Candidate\n")
    out = tmp_path / "packets"

    with pytest.raises(PacketError, match="contains 'jane candidate'|contains 'Jane Candidate'"):
        build_packet(session, "S-001", toy, "R1", out, ["Jane Candidate"])

    assert list(out.iterdir()) == []
    assert not (session / "reviews" / "assignments.csv").exists()


def test_check_packet_catches_forbidden_files_and_names_in_paths(tmp_path):
    (tmp_path / "grading").mkdir()
    (tmp_path / "grading" / "grade.json").write_bytes(b"{}")
    (tmp_path / "meta.yaml").write_bytes(b"x")
    (tmp_path / "jane-notes.md").write_bytes(b"fine")

    problems = check_packet(tmp_path, ["Jane"])

    assert "grading: this file must never be in a packet" in problems
    assert "meta.yaml: this file must never be in a packet" in problems
    assert "jane-notes.md: contains 'Jane'" in problems
    assert "meta.yaml: this file must never be in a packet" in problems
