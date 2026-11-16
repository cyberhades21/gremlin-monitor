"""Match a candidate's findings to checklist items by location.

This is step 1 of the matching rule in docs/specs/02. It decides nothing about
whether an item was fixed. Findings it cannot settle are passed on to the
rubric grader and then to a person.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from gremlin_assess.models import ChecklistItem, Extra
from workspace_tools.findings_check import Finding

MAX_DISTANCE = 10


@dataclass(frozen=True)
class Match:
    finding_index: int
    # matched:       same file, within range, same category. The item is identified.
    # location_only: right place, but the category differs or no line was given.
    #                Goes to step 2.
    # extra:         matches a known issue outside the checklist. Neutral.
    # unmatched:     a counted category that matches nothing. A false positive
    #                unless step 2 or 3 says otherwise.
    # other:         category 'other' that matches nothing. Never a false positive.
    outcome: str
    item_id: str | None = None
    extra_id: str | None = None
    category_mismatch: bool = False
    distance: int | None = None


def _gap(first: tuple[int, int], second: tuple[int, int]) -> int:
    """Lines between two ranges. Zero when they overlap."""
    if first[1] < second[0]:
        return second[0] - first[1]
    if second[1] < first[0]:
        return first[0] - second[1]
    return 0


def _match_one(finding: Finding, gremlins: list[ChecklistItem], extras: list[Extra]) -> Match:
    has_lines = finding.start_line is not None and finding.end_line is not None

    if has_lines:
        span = (finding.start_line, finding.end_line)
        candidates = []
        for item in gremlins:
            for location in item.evidence_location:
                if location.path != finding.path:
                    continue
                distance = _gap(span, location.line_range())
                if distance <= MAX_DISTANCE:
                    candidates.append((distance, item.category != finding.category, item.id))
        if candidates:
            distance, mismatch, item_id = min(candidates)
            return Match(
                finding.index,
                "location_only" if mismatch else "matched",
                item_id=item_id,
                category_mismatch=mismatch,
                distance=distance,
            )
        for extra in extras:
            if extra.location.path == finding.path and _gap(span, extra.location.line_range()) <= MAX_DISTANCE:
                return Match(finding.index, "extra", extra_id=extra.id)
    else:
        in_file = sorted(
            {item.id for item in gremlins if any(loc.path == finding.path for loc in item.evidence_location)}
        )
        if in_file:
            # Without a line we cannot tell which item is meant when a file holds several.
            return Match(finding.index, "location_only", item_id=in_file[0] if len(in_file) == 1 else None)

    return Match(finding.index, "other" if finding.category == "other" else "unmatched")


def match_findings(
    findings: Iterable[Finding], checklist: Iterable[ChecklistItem], extras: Iterable[Extra]
) -> list[Match]:
    gremlins = [item for item in checklist if item.kind == "gremlin"]
    known_extras = list(extras)
    return [_match_one(finding, gremlins, known_extras) for finding in findings]


def identified_items(matches: Iterable[Match]) -> set[str]:
    """Items identified by step 1 alone."""
    return {match.item_id for match in matches if match.outcome == "matched" and match.item_id}
