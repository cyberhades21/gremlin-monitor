"""Schemas for package files. See docs/specs/01, 02 and 05."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

ALLOWED_LICENCES = ("MIT", "BSD-2-Clause", "BSD-3-Clause")
CATEGORIES = ("bug", "doc_error", "test_error")


def _hash_must_be_text(value):
    # YAML reads a hash made only of digits, or digits with one 'e', as a
    # number, which changes it. Refuse it instead of guessing.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise ValueError("write the hash in quotes, because YAML reads some hashes as numbers")
    return value


Sha256 = Annotated[str, BeforeValidator(_hash_must_be_text), Field(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    # Unknown keys are almost always typos, so they are errors.
    model_config = ConfigDict(extra="forbid")


# --- package.yaml -----------------------------------------------------------


class Upstream(_Strict):
    url: str
    commit: str
    licence: Literal["MIT", "BSD-2-Clause", "BSD-3-Clause"]
    licence_file: str
    licence_file_sha256: Sha256


class SessionSpec(_Strict):
    time_limit_minutes: int = Field(gt=0)
    tasks: list[str] = Field(min_length=1)


class DependencyCache(_Strict):
    path: str
    sha256: Sha256


class Dependencies(_Strict):
    lockfile: str
    cache: DependencyCache | None = None


class SystemPackage(_Strict):
    name: str
    version: str


class EditorExtension(_Strict):
    id: str
    version: str
    sha256: Sha256
    source: Literal["open-vsx"]


class Resources(_Strict):
    cpu: float = Field(gt=0)
    memory: str


class Environment(_Strict):
    toolchain: str
    base_image: str = Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")
    system_packages: list[SystemPackage] = []
    dependencies: Dependencies
    build_command: str | None = None
    test_command: str
    results_format: Literal["junit-xml"]
    editor_extensions: list[EditorExtension] = []
    resources: Resources
    network: Literal["none"]


class PackageMeta(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    content_hash: str | None = None
    candidate_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    language: str
    upstream: Upstream
    session: SessionSpec
    agent_config: str
    protected_paths: list[str] = []
    environment: Environment
    image_digest: str | None = None


# --- hidden/checklist.yaml --------------------------------------------------


class EvidenceLocation(_Strict):
    path: str
    lines: str = Field(pattern=r"^\d+(-\d+)?$")

    def line_range(self) -> tuple[int, int]:
        start, _, end = self.lines.partition("-")
        return int(start), int(end or start)


class ChecklistItem(_Strict):
    id: str = Field(pattern=r"^[A-Z]{1,2}-\d{3}$")
    kind: Literal["gremlin", "task"]
    category: Literal["bug", "doc_error", "test_error"] | None = None
    subtype: str | None = None
    contradiction: bool = False
    root_cause_id: str | None = None
    summary: str = Field(min_length=1)
    intended_truth: Literal["code", "doc", "test"] | None = None
    truth_statement: str | None = None
    truth_evidence: list[str] = []
    evidence_location: list[EvidenceLocation] = []
    grading: Literal["test", "rubric"]
    tests: list[str] = []
    rubric_ref: str | None = None
    depends_on: list[str] = []
    agent_alone: dict | None = None

    @model_validator(mode="after")
    def _check(self) -> "ChecklistItem":
        if self.kind == "gremlin":
            missing = [
                name
                for name in ("category", "root_cause_id", "intended_truth", "truth_statement")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"a gremlin needs {', '.join(missing)}")
            if not self.evidence_location:
                raise ValueError("a gremlin needs at least one evidence_location")
            if not self.truth_evidence:
                raise ValueError("a gremlin needs at least one truth_evidence entry")
            if self.contradiction and len(self.truth_evidence) < 2:
                raise ValueError("a contradiction needs at least two truth_evidence entries")
            # The category names the side that is wrong, so the right side must differ.
            if self.category == "bug" and self.intended_truth == "code":
                raise ValueError("a bug means the code is wrong, so intended_truth cannot be 'code'")
            if self.category == "test_error" and self.intended_truth == "test":
                raise ValueError("a test_error means the test is wrong, so intended_truth cannot be 'test'")
        elif self.category is not None:
            raise ValueError("a task has no category")

        if self.grading == "test" and not self.tests:
            raise ValueError("grading 'test' needs at least one entry in tests")
        if self.grading == "rubric" and not self.rubric_ref:
            raise ValueError("grading 'rubric' needs a rubric_ref")
        return self


# --- hidden/extras.yaml, rubric.yaml, walkthrough.yaml ----------------------


class Extra(_Strict):
    id: str = Field(pattern=r"^X-\d{3}$")
    summary: str = Field(min_length=1)
    location: EvidenceLocation
    found_by: Literal["audit", "candidate"]
    verified_on_upstream: bool
    upstream_mappable: bool
    status: Literal["open", "fixed_in_next_version"]


class RubricCriterion(_Strict):
    id: str = Field(pattern=r"^R-\d{3}$")
    item: str
    criterion: str = Field(min_length=1)
    anchors: dict[int, str]
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "RubricCriterion":
        if set(self.anchors) != {0, 1, 2, 3}:
            raise ValueError("anchors must describe exactly the scores 0, 1, 2 and 3")
        return self


class SmallChange(_Strict):
    id: str = Field(pattern=r"^W-\d+$")
    ask: str = Field(min_length=1)
    looks_right_when: str = Field(min_length=1)
    time_box_minutes: int = Field(gt=0)


class Walkthrough(_Strict):
    explain_prompts: list[str] = Field(min_length=2)
    small_changes: list[SmallChange] = Field(min_length=1)
