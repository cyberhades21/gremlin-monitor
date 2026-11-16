import pytest
import yaml

from gremlin_assess.brief import check_brief, disclosed_counts, render_counts_table
from gremlin_assess.hashing import content_hash
from gremlin_assess.package import PackageError, load_package, split_test_ref


def _edit_checklist(root, item_id, **changes):
    path = root / "hidden" / "checklist.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for entry in data:
        if entry["id"] == item_id:
            entry.update(changes)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_toy_loads(toy):
    assert [item.id for item in toy.checklist] == ["G-001", "G-002", "G-003", "G-004", "F-001", "A-001"]
    assert toy.meta.candidate_name == "tallybook"
    assert len(toy.extras) == 1 and len(toy.rubric) == 4 and toy.walkthrough is not None
    assert [item.id for item in toy.test_backed()] == ["G-001", "G-003", "F-001"]


def test_counts_come_from_the_checklist(toy):
    counts = disclosed_counts(toy.checklist)
    assert counts == {"bug": 2, "doc_error": 1, "test_error": 1}
    assert "| Bugs | 2 |" in render_counts_table(counts)
    assert check_brief((toy.public_dir / "BRIEF.md").read_text(encoding="utf-8"), counts) == []


def test_brief_with_a_wrong_count_is_caught(toy):
    brief = (toy.public_dir / "BRIEF.md").read_text(encoding="utf-8").replace("| Bugs | 2 |", "| Bugs | 3 |")
    assert check_brief(brief, disclosed_counts(toy.checklist)) == [
        "BRIEF.md: counts table must contain the row '| Bugs | 2 |'"
    ]


def test_brief_with_reworded_sentences_is_caught(toy):
    brief = (toy.public_dir / "BRIEF.md").read_text(encoding="utf-8")
    brief = brief.replace("The assistant can be wrong.", "The assistant is sometimes wrong.")
    brief = brief.replace("One underlying cause is one issue", "One cause is one issue")
    problems = check_brief(brief, disclosed_counts(toy.checklist))
    assert len(problems) == 2
    assert "assistant can be wrong" in problems[0] and "glossary line" in problems[1]


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"intended_truth": "code"}, "a bug means the code is wrong"),
        ({"truth_evidence": ["README.md#L17"]}, "a contradiction needs at least two truth_evidence entries"),
        ({"tests": []}, "grading 'test' needs at least one entry in tests"),
        ({"root_cause_id": None}, "a gremlin needs root_cause_id"),
        ({"severity": "high"}, "severity"),
        ({"id": "g1"}, "id"),
    ],
)
def test_checklist_rules(toy_copy, changes, expected):
    _edit_checklist(toy_copy, "G-001", **changes)
    with pytest.raises(PackageError) as caught:
        load_package(toy_copy)
    assert any("checklist.yaml item" in problem and expected in problem for problem in caught.value.problems)


def test_a_task_has_no_category(toy_copy):
    _edit_checklist(toy_copy, "F-001", category="bug")
    with pytest.raises(PackageError) as caught:
        load_package(toy_copy)
    assert caught.value.problems == ["checklist.yaml item F-001: a task has no category"]


def test_a_missing_checklist_is_reported(toy_copy):
    (toy_copy / "hidden" / "checklist.yaml").unlink()
    with pytest.raises(PackageError) as caught:
        load_package(toy_copy)
    assert caught.value.problems == ["checklist.yaml: file is missing"]


def test_content_hash_ignores_validation_output_and_its_own_field(toy_copy):
    before = content_hash(toy_copy)
    (toy_copy / "validation" / "report.json").write_text("{}", encoding="utf-8")
    meta = toy_copy / "package.yaml"
    meta.write_text(
        meta.read_text(encoding="utf-8").replace("content_hash: null", "content_hash: sha256:abc"), encoding="utf-8"
    )
    assert content_hash(toy_copy) == before

    (toy_copy / "public" / "repo" / "README.md").write_bytes(b"changed\n")
    assert content_hash(toy_copy) != before


def test_split_test_ref():
    assert split_test_ref("hidden/tests/items/test_a.py") == ("items/test_a.py", None)
    assert split_test_ref("hidden/tests/items/test_a.py::test_x") == ("items/test_a.py", "test_x")
    assert split_test_ref("items\\test_a.py") == ("items/test_a.py", None)
