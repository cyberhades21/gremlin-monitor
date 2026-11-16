"""Format check for a findings file.

Checks the shape of the file only. It never says whether a finding is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

CATEGORIES = ("bug", "doc_error", "test_error", "other")
STATUSES = ("fixed", "found_not_fixed")
FIELDS = ("title", "category", "location", "evidence", "status", "fix_summary", "checked_by")
# These may be left empty when nothing was fixed.
MAY_BE_EMPTY = ("fix_summary", "checked_by")

_LOCATION = re.compile(r"^(?P<path>[^:\s][^:]*?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")


@dataclass(frozen=True)
class Finding:
    index: int  # 1-based position in the file
    title: str
    category: str
    path: str
    start_line: int | None
    end_line: int | None
    evidence: str
    status: str
    fix_summary: str
    checked_by: str


def parse_location(text: str) -> tuple[str, int | None, int | None] | None:
    """Split 'path', 'path:12' or 'path:12-20'. Returns None when it does not fit."""
    match = _LOCATION.match(text.strip())
    if not match:
        return None
    path = match["path"].strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    start = int(match["start"]) if match["start"] else None
    end = int(match["end"]) if match["end"] else start
    if start is not None and end is not None and end < start:
        return None
    return path, start, end


def check_findings_text(text: str) -> tuple[list[Finding], list[str]]:
    """Return the findings that parsed and a list of problems, each naming its field."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], [f"the file is not valid YAML: {exc}"]

    if not isinstance(data, dict) or "findings" not in data:
        return [], ["the file must have a top-level 'findings' list"]
    entries = data["findings"]
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return [], ["'findings' must be a list"]

    findings: list[Finding] = []
    problems: list[str] = []
    for index, entry in enumerate(entries, start=1):
        label = f"finding {index}"
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be a set of fields")
            continue

        entry_problems: list[str] = []
        values: dict[str, str] = {}
        for name in FIELDS:
            if name not in entry:
                entry_problems.append(f"{label}: missing field '{name}'")
                continue
            value = entry[name]
            if value is None:
                value = ""
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                entry_problems.append(f"{label}: field '{name}' must be text")
                continue
            value = str(value).strip()
            if not value and name not in MAY_BE_EMPTY:
                entry_problems.append(f"{label}: field '{name}' is empty")
                continue
            values[name] = value
        for name in entry:
            if name not in FIELDS:
                entry_problems.append(f"{label}: unknown field '{name}'")

        if "category" in values and values["category"] not in CATEGORIES:
            entry_problems.append(f"{label}: field 'category' must be one of {', '.join(CATEGORIES)}")
        if "status" in values and values["status"] not in STATUSES:
            entry_problems.append(f"{label}: field 'status' must be one of {', '.join(STATUSES)}")
        if values.get("status") == "fixed" and "fix_summary" in values and not values["fix_summary"]:
            entry_problems.append(f"{label}: field 'fix_summary' is empty, and it is needed when status is fixed")

        location = None
        if "location" in values:
            location = parse_location(values["location"])
            if location is None:
                entry_problems.append(
                    f"{label}: field 'location' must look like path/to/file.py:42 or path/to/file.py:40-44"
                )

        problems.extend(entry_problems)
        if entry_problems or location is None:
            continue
        findings.append(
            Finding(
                index=index,
                title=values["title"],
                category=values["category"],
                path=location[0],
                start_line=location[1],
                end_line=location[2],
                evidence=values["evidence"],
                status=values["status"],
                fix_summary=values["fix_summary"],
                checked_by=values["checked_by"],
            )
        )
    return findings, problems
