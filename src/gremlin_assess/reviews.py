"""Reviewer forms for Gate 1a. See docs/specs/05, "Reviewer protocol".

A reviewer first sees the work without the gremlin list and gives an overall
judgment (step 1). Only after that is submitted do they get the list and give
per-item verdicts, rubric scores and process ratings (step 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from gremlin_assess.models import plain_yes_no
from gremlin_assess.package import Package

PROCESS_IDS = ("P1", "P2", "P3", "P4")
PROCESS_NAMES = {
    "P1": "Ran the tests or the program after an agent change",
    "P2": "Checked an agent claim before relying on it",
    "P3": "Corrected or rejected a wrong agent output",
    "P4": "Resolved a contradiction with evidence",
}


class ReviewError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step1(_Strict):
    reviewer: str = Field(min_length=1)
    packet: str = Field(pattern=r"^P-[0-9a-f]{6}$")
    overall: int = Field(ge=1, le=5)
    reasons: str = Field(min_length=20)


class ItemVerdict(_Strict):
    id: str
    verdict: Annotated[Literal["yes", "partly", "no"], BeforeValidator(plain_yes_no)]


class RubricScore(_Strict):
    id: str
    score: int = Field(ge=0, le=3)


class ProcessRating(_Strict):
    id: Literal["P1", "P2", "P3", "P4"]
    rating: Literal["observed", "not_observed", "cannot_tell"]
    pointer: str | None = None

    @model_validator(mode="after")
    def _pointer(self) -> "ProcessRating":
        if self.rating == "observed" and not (self.pointer or "").strip():
            raise ValueError("an 'observed' rating needs a pointer, such as a time in the recording")
        return self


class Step2(_Strict):
    reviewer: str = Field(min_length=1)
    packet: str = Field(pattern=r"^P-[0-9a-f]{6}$")
    items: list[ItemVerdict]
    rubric: list[RubricScore]
    process: list[ProcessRating]
    minutes: float = Field(gt=0)


# --- templates --------------------------------------------------------------


def step1_template(packet_id: str) -> str:
    return (
        "# Step 1. Do not open the checklist yet.\n"
        "# Judge the work from the brief, the diff, the findings and the recordings.\n"
        f"reviewer: \"\"            # your name or initials\n"
        f"packet: {packet_id}\n"
        "overall: 0              # 1 (poor) to 5 (excellent): how well did this person do the work?\n"
        "reasons: \"\"            # two sentences saying why\n"
    )


def step2_template(package: Package, packet_id: str) -> str:
    lines = [
        "# Step 2. Use the checklist in GREMLIN-LIST.md.",
        'reviewer: ""',
        f"packet: {packet_id}",
        "items:                  # for every item: yes | partly | no",
    ]
    lines += [f"  - {{id: {item.id}, verdict: no}}" for item in package.checklist]
    lines.append("rubric:                 # for every criterion, a score from 0 to 3 using its anchors")
    lines += [f"  - {{id: {criterion.id}, score: 0}}" for criterion in package.rubric]
    lines.append("process:                # observed | not_observed | cannot_tell. 'observed' needs a pointer")
    for pid in PROCESS_IDS:
        lines.append(f"  - {{id: {pid}, rating: cannot_tell, pointer: null}}   # {PROCESS_NAMES[pid]}")
    lines.append("minutes: 0              # how long you spent on this session")
    return "\n".join(lines) + "\n"


# --- loading ----------------------------------------------------------------


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReviewError(f"{path}: could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewError(f"{path}: must be a set of fields")
    return data


def _explain(path: Path, exc: ValidationError) -> ReviewError:
    problems = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"])
        problems.append(f"{where}: {error['msg'].removeprefix('Value error, ')}")
    return ReviewError(f"{path}: " + "; ".join(problems))


def load_step1(path: Path) -> Step1:
    try:
        return Step1.model_validate(_read(path))
    except ValidationError as exc:
        raise _explain(path, exc) from exc


def load_step2(path: Path, package: Package) -> Step2:
    """Load a step 2 form and check it covers exactly this package's items and criteria."""
    try:
        form = Step2.model_validate(_read(path))
    except ValidationError as exc:
        raise _explain(path, exc) from exc

    problems = []
    for name, given, expected in (
        ("items", [i.id for i in form.items], [i.id for i in package.checklist]),
        ("rubric", [r.id for r in form.rubric], [c.id for c in package.rubric]),
        ("process", [p.id for p in form.process], list(PROCESS_IDS)),
    ):
        if sorted(given) != sorted(expected):
            problems.append(f"{name} must cover exactly {', '.join(sorted(expected))}")
    if problems:
        raise ReviewError(f"{path}: " + "; ".join(problems))
    return form
