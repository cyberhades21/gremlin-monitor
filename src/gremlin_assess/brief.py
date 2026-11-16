"""Counts and required wording for BRIEF.md. See docs/specs/01 and 02.

The counts shown to candidates are always derived from the checklist. Nobody
types them by hand.
"""

from __future__ import annotations

from collections.abc import Iterable

from gremlin_assess.models import ChecklistItem

CATEGORY_LABELS = {
    "bug": "Bugs",
    "doc_error": "Documentation errors",
    "test_error": "Test errors",
}

SCORING_SENTENCE = (
    "The counts above are the issues we score. If you find anything else, list it under 'other'. "
    "It will not count for or against you."
)
ASSISTANT_SENTENCE = "The assistant can be wrong. Check its work the way you would check a colleague's."
GLOSSARY = (
    "Bug. The code does something other than what it is meant to do.",
    "Documentation error. A document or comment says something untrue about the intended behaviour, "
    "points to something that does not exist, or contradicts another document.",
    "Test error. A test that passes without checking what it claims to check, or that asserts the "
    "wrong behaviour.",
    "When a document and the code disagree, work out which one reflects the intended behaviour. "
    "Use the tests, the other documents and how the code is used. If the code is wrong, it is a bug. "
    "If the document is wrong, it is a documentation error.",
    "One underlying cause is one issue, however many places it shows up.",
)


def disclosed_counts(checklist: Iterable[ChecklistItem]) -> dict[str, int]:
    """Number of scored gremlins per category. Tasks are not counted."""
    counts = {category: 0 for category in CATEGORY_LABELS}
    for item in checklist:
        if item.kind == "gremlin" and item.category:
            counts[item.category] += 1
    return counts


def _row(category: str, count: int) -> str:
    return f"| {CATEGORY_LABELS[category]} | {count} |"


def render_counts_table(counts: dict[str, int]) -> str:
    lines = ["| Category | Scored issues |", "|---|---|"]
    lines += [_row(category, counts[category]) for category in CATEGORY_LABELS]
    return "\n".join(lines) + "\n"


def _normalise(text: str) -> str:
    # Line wrapping and bold markers must not break a word-for-word check.
    return " ".join(text.replace("*", "").split())


def check_brief(text: str, counts: dict[str, int]) -> list[str]:
    """Return what BRIEF.md is missing. An empty list means it passes."""
    brief = _normalise(text)
    problems = []
    for category in CATEGORY_LABELS:
        if _normalise(_row(category, counts[category])) not in brief:
            problems.append(
                f"BRIEF.md: counts table must contain the row '{_row(category, counts[category])}'"
            )
    if _normalise(SCORING_SENTENCE) not in brief:
        problems.append("BRIEF.md: the scoring sentence is missing or reworded")
    if _normalise(ASSISTANT_SENTENCE) not in brief:
        problems.append("BRIEF.md: the sentence saying the assistant can be wrong is missing or reworded")
    for line in GLOSSARY:
        if _normalise(line) not in brief:
            problems.append(f"BRIEF.md: glossary line is missing or reworded: '{line[:40]}...'")
    return problems
