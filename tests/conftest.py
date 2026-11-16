import shutil
from pathlib import Path

import pytest

from gremlin_assess.package import Package, load_package
from gremlin_assess.patches import apply_patches

TOY = Path(__file__).parent / "fixtures" / "packages" / "toy"


@pytest.fixture(scope="session")
def toy() -> Package:
    return load_package(TOY)


@pytest.fixture
def toy_copy(tmp_path: Path) -> Path:
    """A private copy of the toy package that a test may change."""
    dest = tmp_path / "toy"
    shutil.copytree(TOY, dest)
    return dest


@pytest.fixture
def repo_with(tmp_path: Path, toy: Package):
    """Build a copy of the flawed repo with some reference patches applied."""
    counter = iter(range(1000))

    def build(*item_ids: str) -> Path:
        dest = tmp_path / f"repo-{next(counter)}"
        shutil.copytree(toy.repo_dir, dest)
        apply_patches(dest, [toy.patch_for(item_id) for item_id in item_ids])
        return dest

    return build
