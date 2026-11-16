"""Load a package directory into typed objects. See docs/specs/01 and 02."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from gremlin_assess.models import ChecklistItem, Extra, PackageMeta, RubricCriterion, Walkthrough

TESTS_PREFIX = "hidden/tests/"

M = TypeVar("M", bound=BaseModel)


class PackageError(Exception):
    """The package files could not be read as a package."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def split_test_ref(ref: str) -> tuple[str, str | None]:
    """Turn 'hidden/tests/items/test_x.py::test_name' into ('items/test_x.py', 'test_name')."""
    path, _, name = ref.partition("::")
    path = path.replace("\\", "/")
    if path.startswith(TESTS_PREFIX):
        path = path[len(TESTS_PREFIX) :]
    return path, (name or None)


@dataclass(frozen=True)
class Package:
    root: Path
    meta: PackageMeta
    checklist: tuple[ChecklistItem, ...]
    extras: tuple[Extra, ...]
    rubric: tuple[RubricCriterion, ...]
    walkthrough: Walkthrough | None

    @property
    def public_dir(self) -> Path:
        return self.root / "public"

    @property
    def repo_dir(self) -> Path:
        return self.root / "public" / "repo"

    @property
    def hidden_dir(self) -> Path:
        return self.root / "hidden"

    @property
    def tests_dir(self) -> Path:
        return self.root / "hidden" / "tests"

    @property
    def reference_dir(self) -> Path:
        return self.root / "hidden" / "reference"

    @property
    def validation_dir(self) -> Path:
        return self.root / "validation"

    def item(self, item_id: str) -> ChecklistItem:
        for item in self.checklist:
            if item.id == item_id:
                return item
        raise KeyError(item_id)

    def gremlins(self) -> list[ChecklistItem]:
        return [item for item in self.checklist if item.kind == "gremlin"]

    def test_backed(self) -> list[ChecklistItem]:
        return [item for item in self.checklist if item.grading == "test"]

    def patch_for(self, item_id: str) -> Path:
        return self.reference_dir / f"{item_id}.patch"


def _explain(label: str, exc: ValidationError) -> list[str]:
    problems = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        problems.append(f"{label}: {where}: {message}" if where else f"{label}: {message}")
    return problems


def _read_yaml(path: Path, label: str, problems: list[str], required: bool):
    if not path.is_file():
        if required:
            problems.append(f"{label}: file is missing")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problems.append(f"{label}: not valid YAML ({exc})")
        return None


def _load_one(path: Path, model: type[M], label: str, problems: list[str], required: bool = True) -> M | None:
    data = _read_yaml(path, label, problems, required)
    if data is None:
        return None
    if not isinstance(data, dict):
        problems.append(f"{label}: must be a set of fields")
        return None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems.extend(_explain(label, exc))
        return None


def _load_list(path: Path, model: type[M], label: str, problems: list[str], required: bool = True) -> tuple[M, ...]:
    data = _read_yaml(path, label, problems, required)
    if data is None:
        return ()
    if not isinstance(data, list):
        problems.append(f"{label}: must be a list")
        return ()
    loaded = []
    for index, entry in enumerate(data, start=1):
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        entry_label = f"{label} item {entry_id or index}"
        if not isinstance(entry, dict):
            problems.append(f"{entry_label}: must be a set of fields")
            continue
        try:
            loaded.append(model.model_validate(entry))
        except ValidationError as exc:
            problems.extend(_explain(entry_label, exc))
    return tuple(loaded)


def load_package(root: Path) -> Package:
    """Read a package directory. Raises PackageError listing every problem found."""
    root = Path(root)
    problems: list[str] = []
    if not root.is_dir():
        raise PackageError([f"{root}: not a directory"])

    meta = _load_one(root / "package.yaml", PackageMeta, "package.yaml", problems)
    checklist = _load_list(root / "hidden" / "checklist.yaml", ChecklistItem, "checklist.yaml", problems)
    extras = _load_list(root / "hidden" / "extras.yaml", Extra, "extras.yaml", problems, required=False)
    rubric = _load_list(root / "hidden" / "rubric.yaml", RubricCriterion, "rubric.yaml", problems, required=False)
    walkthrough = _load_one(
        root / "hidden" / "walkthrough.yaml", Walkthrough, "walkthrough.yaml", problems, required=False
    )

    if problems or meta is None:
        raise PackageError(problems)
    return Package(
        root=root, meta=meta, checklist=checklist, extras=extras, rubric=rubric, walkthrough=walkthrough
    )
