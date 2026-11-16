"""Result types for a grading run. See docs/specs/05."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False  # not a test class, despite the name

    file: str  # posix path under hidden/tests
    name: str
    outcome: str  # passed | failed | error | skipped
    message: str = ""


@dataclass(frozen=True)
class Flag:
    kind: str
    detail: str
    path: str | None = None
    line: int | None = None

    def describe(self) -> str:
        where = f" ({self.path}" + (f":{self.line}" if self.line else "") + ")" if self.path else ""
        return f"{self.kind}: {self.detail}{where}"


@dataclass
class ItemResult:
    item_id: str
    # yes | no | needs_review for test-backed items. not_graded for rubric items,
    # which are scored by the rubric grader.
    fixed: str
    tests: list[TestOutcome] = field(default_factory=list)


@dataclass
class GradeResult:
    items: dict[str, ItemResult]
    guards: list[TestOutcome]
    flags: list[Flag]
    # False when the run itself cannot be trusted. Every test-backed result is
    # then needs_review.
    integrity_ok: bool
    exit_code: int | None
    timed_out: bool
    seen_tests: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[TestOutcome]:
        return [guard for guard in self.guards if guard.outcome != "passed"]

    def to_dict(self) -> dict:
        return {
            "items": {item_id: asdict(result) for item_id, result in sorted(self.items.items())},
            "regressions": [asdict(guard) for guard in self.regressions],
            "flags": [asdict(flag) for flag in self.flags],
            "integrity_ok": self.integrity_ok,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
        }
