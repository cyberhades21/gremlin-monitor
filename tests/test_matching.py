from gremlin_assess.matching import identified_items, match_findings
from workspace_tools.findings_check import Finding


def finding(index, category, path, start=None, end=None):
    return Finding(index, "title", category, path, start, end or start, "evidence", "found_not_fixed", "", "")


def test_findings_are_matched_by_location(toy):
    findings = [
        finding(1, "bug", "tallybook/limits.py", 11),  # right place, right category
        finding(2, "doc_error", "tallybook/limits.py", 11),  # right place, wrong category
        finding(3, "doc_error", "README.md", 19),
        finding(4, "bug", "tallybook/ledger.py", 20),  # a known extra
        finding(5, "bug", "docs/limits.md", 1),  # matches nothing
        finding(6, "other", "docs/limits.md", 1),
        finding(7, "bug", "tallybook/limits.py"),  # no line number
        finding(8, "bug", "tallybook/limits.py", 21),  # exactly 10 lines away
        finding(9, "bug", "tallybook/limits.py", 22),  # 11 lines away
        finding(10, "test_error", "tests/test_pricing.py", 12),  # inside a range
    ]
    matches = match_findings(findings, toy.checklist, toy.extras)

    assert [(match.outcome, match.item_id or match.extra_id) for match in matches] == [
        ("matched", "G-001"),
        ("location_only", "G-001"),
        ("matched", "G-002"),
        ("extra", "X-001"),
        ("unmatched", None),
        ("other", None),
        ("location_only", "G-001"),
        ("matched", "G-001"),
        ("unmatched", None),
        ("matched", "G-004"),
    ]
    assert matches[1].category_mismatch and not matches[0].category_mismatch
    assert matches[7].distance == 10
    assert identified_items(matches) == {"G-001", "G-002", "G-004"}


def test_matching_is_repeatable(toy):
    findings = [finding(1, "bug", "tallybook/pricing.py", 5, 9), finding(2, "bug", "tallybook/limits.py", 1)]
    first = match_findings(findings, toy.checklist, toy.extras)
    assert first == match_findings(findings, toy.checklist, toy.extras)
    assert [(match.outcome, match.item_id, match.distance) for match in first] == [
        ("matched", "G-003", 2),
        ("matched", "G-001", 10),
    ]
