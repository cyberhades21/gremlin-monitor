"""The Gate 1a arithmetic on small, hand-checkable data."""

import pytest

from gremlin_assess.analysis import (
    ReviewData,
    SessionData,
    compute_gate,
    grader_vs_reviewers,
    item_agreement,
    overall_check,
    render_gate,
    reviewer_ceiling,
    rubric_pass_agreement,
    spearman,
)


def session(name="S-001", fixed=None, **options):
    fixed = fixed or {"A": "yes", "B": "no", "C": "yes"}
    return SessionData(
        session=name, package_id="pkg", agent_source=options.pop("agent_source", "ours"),
        our_fixed=fixed, test_backed=options.pop("test_backed", {"A", "B"}),
        rubric_scores=options.pop("rubric_scores", {}), rubric_median=options.pop("rubric_median", {}),
        cost_usd=options.pop("cost_usd", None),
    )


def review(reviewer, verdicts, name="S-001", **options):
    return ReviewData(name, reviewer, verdicts=verdicts, **options)


def test_spearman():
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [1, 2, 3]) == pytest.approx(0.8660, abs=1e-4)  # ties share a rank
    assert spearman([1, 2], [1, 2]) is None  # too few pairs
    assert spearman([5, 5, 5], [1, 2, 3]) is None  # no variation


def test_item_agreement_counts_partly_as_no_and_skips_unsettled_items():
    sessions = [session(fixed={"A": "yes", "B": "no", "C": "yes"})]
    reviews = [review("R1", {"A": "yes", "B": "no"}), review("R2", {"A": "partly", "B": "no"})]

    n, agree, disagreements = item_agreement(sessions, reviews)

    assert (n, agree) == (4, 3)
    assert disagreements == [("S-001", "A", "yes", "partly")]

    unsettled = [session(fixed={"A": "needs_review", "B": "no"})]
    assert item_agreement(unsettled, reviews)[:2] == (2, 2)  # only B counts


def test_item_agreement_ignores_other_sessions_and_empty_forms():
    reviews = [review("R1", {"A": "yes"}, name="S-009"), review("R2", {})]
    assert item_agreement([session()], reviews)[:2] == (0, 0)


def test_the_ceiling_compares_every_pair_of_reviewers():
    pair = [review("R1", {"A": "yes", "B": "no"}), review("R2", {"A": "partly", "B": "no"})]
    assert reviewer_ceiling([session()], pair) == (2, 1)
    assert reviewer_ceiling([session()], [*pair, review("R3", {"A": "yes", "B": "no"})]) == (6, 4)
    assert reviewer_ceiling([session()], pair[:1]) == (0, 0)


def test_rubric_pass_agreement_only_counts_criteria_with_all_passes():
    data = session(rubric_scores={"R-1": [3, 3, 2], "R-2": [3, 1, 1], "R-3": [2, 2]})
    assert rubric_pass_agreement([data]) == (2, 1)


def test_grader_against_reviewers():
    data = session(rubric_median={"R-1": 3, "R-2": 0, "R-3": None})
    reviews = [review("R1", {}, rubric={"R-1": 3, "R-2": 2}), review("R2", {}, rubric={"R-1": 2})]

    n, mean, within = grader_vs_reviewers([data], reviews)

    assert (n, mean, within) == (2, pytest.approx(1.25), 0.5)  # |3-2.5| and |0-2|
    assert grader_vs_reviewers([session()], reviews) == (0, None, None)


def test_overall_check_needs_three_sessions():
    sessions = [session("S-001", {"A": "yes", "B": "yes"}), session("S-002", {"A": "yes"}), session("S-003", {"A": "no"})]

    n, corr = overall_check(sessions, {"S-001": [5, 4], "S-002": [3], "S-003": [1]})
    assert (n, corr) == (3, pytest.approx(1.0))
    assert overall_check(sessions[:2], {"S-001": [5], "S-002": [3]}) == (2, None)


def build_gate(agree_of_ten):
    sessions = [session("S-001", test_backed={f"I{i}" for i in range(10)}, fixed={f"I{i}": "yes" for i in range(10)})]
    verdicts = {f"I{i}": ("yes" if i < agree_of_ten else "no") for i in range(10)}
    return compute_gate(sessions, [review("R1", verdicts)], {})


def test_item_agreement_threshold_is_at_least_90_percent():
    by_name = lambda gate: {m.name: m for m in gate.measures}["Item agreement (ours against reviewers)"]

    assert by_name(build_gate(9)).status == "met"  # exactly 90%
    assert by_name(build_gate(8)).status == "not_met"
    assert by_name(build_gate(9)).shown() == "90.0%"


def test_closeness_threshold_allows_five_points_below_the_ceiling():
    # Ceiling 100% (reviewers agree), ours 90% -> 10 points below: not met
    sessions = [session("S-001", test_backed={f"I{i}" for i in range(10)}, fixed={f"I{i}": "yes" for i in range(10)})]
    wrong = {f"I{i}": ("yes" if i < 9 else "no") for i in range(10)}
    gate = compute_gate(sessions, [review("R1", wrong), review("R2", wrong)], {})
    closeness = {m.name: m for m in gate.measures}["Closeness (item agreement minus ceiling)"]

    assert closeness.status == "not_met" and closeness.shown() == "-10.0 points"


def test_nothing_is_claimed_without_data():
    gate = compute_gate([session()], [], {})

    statuses = {m.name: m.status for m in gate.measures}
    assert statuses["Item agreement (ours against reviewers)"] == "not_measured"
    assert statuses["Agent alone (full solves in 5 runs)"] == "not_measured"
    assert statuses["Cost per session (our agent only)"] == "not_measured"
    assert all(s != "met" for s in statuses.values())


def test_own_agent_sessions_are_left_out_of_cost_only():
    sessions = [session("S-001", cost_usd=2.0), session("S-002", cost_usd=4.0), session("S-003", agent_source="own-codex", cost_usd=99.0)]

    cost = {m.name: m for m in compute_gate(sessions, [], {}).measures}["Cost per session (our agent only)"]

    assert (cost.value, cost.n) == (3.0, 2) and "1 sessions used the candidate's own agent" in cost.note


def test_the_rendered_report_states_its_limits_and_lists_disagreements():
    gate = compute_gate([session()], [review("R1", {"A": "partly", "B": "no"})], {})

    text = render_gate(gate, {"pkg-a": {"flaw_design": 3.0, "approval": 1.5}}, (0, None))

    assert "directional, not statistical proof" in text
    assert "which changes difficulty and is not measured here" in text
    assert "- pkg-a: 4.5 hours (approval 1.5, flaw_design 3)" in text
    assert "No survey answers found." in text
    assert "| S-001 | A | yes | partly | |" in text
