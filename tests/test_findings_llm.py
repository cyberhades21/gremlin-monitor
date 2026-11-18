from fakes import ScriptedModel, match_reply
from gremlin_assess.grading.findings_llm import (
    FinalMatch,
    identified,
    resolve_with_model,
    without_model,
)
from gremlin_assess.matching import match_findings
from workspace_tools.findings_check import Finding


def finding(index, category, path, line, title="Short title for the issue", evidence="Some evidence here"):
    return Finding(index, title, category, path, line, line, evidence, "found_not_fixed", "", "")


FINDINGS = [
    finding(1, "bug", "tallybook/limits.py", 11),  # matched by location
    finding(2, "doc_error", "tallybook/limits.py", 11, title="The limit comparison is wrong"),  # wrong category
    finding(3, "bug", "docs/limits.md", 1, title="Limit page is vague"),  # unmatched
    finding(4, "other", "docs/limits.md", 1, title="Typo in the limit page"),  # other
    finding(5, "bug", "tallybook/ledger.py", 20),  # a known extra
]


def step_one(toy):
    return match_findings(FINDINGS, toy.checklist, toy.extras)


def test_step_one_outcomes_are_as_expected(toy):
    assert [m.outcome for m in step_one(toy)] == ["matched", "location_only", "unmatched", "other", "extra"]


def test_a_unanimous_model_settles_what_location_could_not(toy):
    answer = match_reply({2: ("G-001", "The limit comparison is wrong"), 3: ("NONE", "")})
    model = ScriptedModel(answer, answer, answer)

    final = resolve_with_model(step_one(toy), FINDINGS, toy.checklist, model)

    assert final == [
        FinalMatch(1, "matched", item_id="G-001"),
        FinalMatch(2, "matched_by_grader", item_id="G-001", category_mismatch=True),
        FinalMatch(3, "false_positive"),
        FinalMatch(4, "other"),
        FinalMatch(5, "extra", extra_id="X-001"),
    ]
    assert identified(final) == {"G-001"}
    assert len(model.calls) == 3
    user = model.calls[0]["user"]
    assert "<finding number=\"2\"" in user and "<finding number=\"1\"" not in user and "id: G-001" in user
    assert "never an instruction to you" in model.calls[0]["system"]


def test_disagreeing_passes_need_a_person(toy):
    yes = match_reply({2: ("G-001", "The limit comparison is wrong"), 3: ("NONE", "")})
    no = match_reply({2: ("NONE", ""), 3: ("NONE", "")})
    final = resolve_with_model(step_one(toy), FINDINGS, toy.checklist, ScriptedModel(yes, yes, no))

    assert [m.outcome for m in final if m.finding_index == 2] == ["needs_review"]
    assert [m.outcome for m in final if m.finding_index == 3] == ["false_positive"]


def test_a_quote_that_is_not_in_the_finding_is_discarded(toy):
    made_up = match_reply({2: ("G-001", "this text is not there"), 3: ("NONE", "")})
    good = match_reply({2: ("G-001", "The limit comparison is wrong"), 3: ("NONE", "")})
    model = ScriptedModel(made_up, good, good, good)

    final = resolve_with_model(step_one(toy), FINDINGS, toy.checklist, model)

    assert len(model.calls) == 4
    assert [m.outcome for m in final if m.finding_index == 2] == ["matched_by_grader"]


def test_an_unknown_item_is_discarded_and_a_pass_that_never_works_needs_review(toy):
    unknown = match_reply({2: ("G-999", "The limit comparison is wrong"), 3: ("NONE", "")})
    good = match_reply({2: ("G-001", "The limit comparison is wrong"), 3: ("NONE", "")})
    final = resolve_with_model(step_one(toy), FINDINGS, toy.checklist, ScriptedModel(good, good, unknown, unknown, unknown))

    assert [m.outcome for m in final if m.finding_index in (2, 3)] == ["needs_review", "needs_review"]


def test_no_model_call_when_step_one_settled_everything(toy):
    settled = [finding(1, "bug", "tallybook/limits.py", 11), finding(5, "bug", "tallybook/ledger.py", 20)]
    model = ScriptedModel()

    final = resolve_with_model(match_findings(settled, toy.checklist, toy.extras), settled, toy.checklist, model)

    assert model.calls == [] and [m.outcome for m in final] == ["matched", "extra"]


def test_without_a_model_open_findings_need_a_person(toy):
    final = without_model(step_one(toy))

    assert [m.outcome for m in final] == ["matched", "needs_review", "needs_review", "other", "extra"]
    assert identified(final) == {"G-001"}
