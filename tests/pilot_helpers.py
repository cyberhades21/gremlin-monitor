"""Build realistic pilot sessions from the toy package."""

import yaml

from fakes import ARCH_Q1, ARCH_Q2, README_Q, ScriptedModel, match_reply, rubric_reply
from gremlin_assess.export import export_public
from gremlin_assess.patches import apply_patches
from gremlin_assess.pipeline import grade_session
from gremlin_assess.session import collect, new_session, start_session
from workspace_tools.submit import submit

ARCHITECTURE = (
    "Tallybook has three modules: ledger.py keeps entries, limits.py checks purchases\n"
    "against a limit, pricing.py applies discounts. A purchase equal to the limit is rejected.\n"
)


def finding(title, category, location, status="fixed"):
    return {
        "title": title,
        "category": category,
        "location": location,
        "evidence": "read the code and the docs",
        "status": status,
        "fix_summary": "changed it" if status == "fixed" else "",
        "checked_by": "ran the tests" if status == "fixed" else "",
    }


def findings_yaml(entries):
    return yaml.safe_dump({"findings": entries}, sort_keys=False).encode("utf-8")


def make_submission(toy, tmp_path, name, patches, findings):
    """Export a workspace, apply reference patches as the candidate's work, and run submit."""
    workspace = tmp_path / name / "workspace"
    repo = export_public(toy, workspace)
    apply_patches(repo, [toy.patch_for(item_id) for item_id in patches])
    (workspace / "FINDINGS.yaml").write_bytes(findings_yaml(findings))
    (workspace / "ARCHITECTURE.md").write_bytes(ARCHITECTURE.encode("utf-8"))
    archive, problems = submit(workspace, tmp_path / name / "submissions")
    assert problems == []
    return archive


ZERO = {c: (0, []) for c in ("R-001", "R-002", "R-003", "R-004")}
FULL = {"R-001": (3, [README_Q]), "R-002": (0, []), "R-003": (3, [ARCH_Q1]), "R-004": (2, [ARCH_Q2])}


def scripted(rubric_scores, *, open_findings=False):
    replies = []
    if open_findings:
        replies += [match_reply({5: ("NONE", "")})] * 3
    replies += [rubric_reply(rubric_scores)] * 3
    return ScriptedModel(*replies)


ITEMS = ("G-001", "G-002", "G-003", "G-004", "F-001", "A-001")
CRITERIA = ("R-001", "R-002", "R-003", "R-004")


def review_forms(packet_id, reviewer, overall, verdicts, rubric):
    """Filled-in step 1 and step 2 forms. `verdicts` and `rubric` map ids to answers."""
    step1 = {"reviewer": reviewer, "packet": packet_id, "overall": overall, "reasons": "A short but real explanation of why."}
    step2 = {
        "reviewer": reviewer,
        "packet": packet_id,
        "items": [{"id": i, "verdict": verdicts.get(i, "no")} for i in ITEMS],
        "rubric": [{"id": c, "score": rubric.get(c, 0)} for c in CRITERIA],
        "process": [
            {"id": "P1", "rating": "observed", "pointer": "00:12"},
            {"id": "P2", "rating": "cannot_tell", "pointer": None},
            {"id": "P3", "rating": "not_observed", "pointer": None},
            {"id": "P4", "rating": "cannot_tell", "pointer": None},
        ],
        "minutes": 20,
    }
    return yaml.safe_dump(step1), yaml.safe_dump(step2)


def run_session(toy, pilot, tmp_path, session_id, candidate, patches, findings, model, **options):
    """Create, collect and grade one session. Returns the session folder."""
    directory = new_session(pilot, session_id, toy, candidate, **options)
    start_session(pilot, session_id)
    (directory / "terminal.cast").write_bytes(b'{"version": 2}\n[0.5, "o", "$ pytest\\r\\n"]\n')
    (directory / "transcript" / "agent.log").write_bytes(b"user: please find the bugs\nassistant: looking\n")
    (directory / "snapshots" / "20261118T100000Z.tar.gz").write_bytes(b"snapshot")
    archive = make_submission(toy, tmp_path, session_id, patches, findings)
    assert collect(pilot, session_id, archive) == []
    grade_session(pilot, session_id, toy, client=model)
    return directory
