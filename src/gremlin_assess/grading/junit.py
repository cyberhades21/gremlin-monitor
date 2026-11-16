"""Read a JUnit XML results file.

The file is written by a process that also ran candidate code, so it is
treated as untrusted input: size-capped, parsed defensively, and checked for
internal consistency.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 20_000_000
_OUTCOME = {"failure": "failed", "error": "error", "skipped": "skipped"}
_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
_DECLARED = {"failure": "failures", "error": "errors", "skipped": "skipped"}


class JUnitError(Exception):
    pass


@dataclass(frozen=True)
class Case:
    file: str | None
    classname: str
    name: str
    outcome: str
    message: str


@dataclass(frozen=True)
class JUnitResult:
    cases: tuple[Case, ...]
    # False when the totals the file declares do not match its own test cases,
    # which happens when something edited the file after the runner wrote it.
    consistent: bool


def parse_junit(path: Path) -> JUnitResult:
    if path.stat().st_size > MAX_BYTES:
        raise JUnitError("the results file is too large to be genuine")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise JUnitError(f"the results file is not valid XML: {exc}") from exc

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise JUnitError("the results file has no test suite")

    cases: list[Case] = []
    counted = dict.fromkeys(_OUTCOME, 0)
    declared = dict.fromkeys(_OUTCOME, 0)
    for suite in suites:
        for tag, attribute in _DECLARED.items():
            try:
                declared[tag] += int(suite.get(attribute) or 0)
            except ValueError as exc:
                raise JUnitError(f"the results file has a non-numeric '{attribute}' total") from exc
        for case in suite.iter("testcase"):
            outcome, message = "passed", ""
            for child in case:
                kind = _OUTCOME.get(child.tag)
                if kind is None:
                    continue
                counted[child.tag] += 1
                if _RANK[kind] > _RANK[outcome]:
                    outcome, message = kind, child.get("message") or ""
            cases.append(
                Case(
                    file=case.get("file"),
                    classname=case.get("classname") or "",
                    name=case.get("name") or "",
                    outcome=outcome,
                    message=message,
                )
            )
    return JUnitResult(cases=tuple(cases), consistent=counted == declared)
