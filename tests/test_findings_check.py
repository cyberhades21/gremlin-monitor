import textwrap

from workspace_tools.findings_check import check_findings_text, parse_location

VALID = textwrap.dedent(
    """
    findings:
      - title: "Orders equal to the limit are accepted"
        category: bug
        location: "tallybook/limits.py:11"
        evidence: "docs/limits.md says at or above"
        status: fixed
        fix_summary: "Changed the comparison"
        checked_by: "ran the tests"
      - title: "README is wrong about balance"
        category: doc_error
        location: "README.md:19-20"
        evidence: "the code returns a Decimal"
        status: found_not_fixed
        fix_summary: ""
        checked_by: ""
    """
)


def test_valid_file_parses():
    findings, problems = check_findings_text(VALID)
    assert problems == []
    assert [(f.index, f.category, f.path, f.start_line, f.end_line) for f in findings] == [
        (1, "bug", "tallybook/limits.py", 11, 11),
        (2, "doc_error", "README.md", 19, 20),
    ]


def test_empty_template_is_valid():
    assert check_findings_text("# comment\nfindings: []\n") == ([], [])


def test_missing_field_is_named():
    text = VALID.replace('    evidence: "docs/limits.md says at or above"\n', "")
    findings, problems = check_findings_text(text)
    assert problems == ["finding 1: missing field 'evidence'"]
    assert [f.index for f in findings] == [2]


def test_bad_values_are_named():
    text = VALID.replace("category: bug", "category: defect").replace("status: fixed", "status: done")
    _, problems = check_findings_text(text)
    assert any("field 'category' must be one of" in p for p in problems)
    assert any("field 'status' must be one of" in p for p in problems)


def test_fixed_needs_a_fix_summary():
    _, problems = check_findings_text(VALID.replace('fix_summary: "Changed the comparison"', 'fix_summary: ""'))
    assert problems == ["finding 1: field 'fix_summary' is empty, and it is needed when status is fixed"]


def test_unknown_field_is_named():
    _, problems = check_findings_text(VALID.replace("    status: fixed\n", "    status: fixed\n    severity: high\n"))
    assert problems == ["finding 1: unknown field 'severity'"]


def test_bad_location_is_named():
    _, problems = check_findings_text(VALID.replace("tallybook/limits.py:11", "tallybook/limits.py:11:5"))
    assert len(problems) == 1 and "field 'location' must look like" in problems[0]


def test_not_yaml_and_wrong_shape():
    assert check_findings_text("findings: [unclosed")[1][0].startswith("the file is not valid YAML")
    assert check_findings_text("- just a list")[1] == ["the file must have a top-level 'findings' list"]
    assert check_findings_text("findings: 3")[1] == ["'findings' must be a list"]


def test_parse_location_forms():
    assert parse_location("a/b.py") == ("a/b.py", None, None)
    assert parse_location("./a\\b.py:7") == ("a/b.py", 7, 7)
    assert parse_location("a/b.py:7-9") == ("a/b.py", 7, 9)
    assert parse_location("a/b.py:9-7") is None
