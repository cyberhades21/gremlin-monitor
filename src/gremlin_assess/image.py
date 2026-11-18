"""Build the container image a package asks for. See docs/specs/01 and 06.

The image holds the toolchain and dependencies only. The repo and the hidden
tests are mounted in when a container runs, so one image serves candidate
sessions, validation and grading, and identical environments share an image.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from gremlin_assess.container import DockerError, build_image, image_exists
from gremlin_assess.hashing import tree_sha256
from gremlin_assess.models import Environment
from gremlin_assess.package import Package

# Tools the platform needs inside every Python image, pinned.
PLATFORM_PIP_PACKAGES = ("pytest==9.2.0",)
CANDIDATE_USER = "candidate"
CANDIDATE_UID = 1000
WHEELS_DIR = "environment/wheels"


class ImageError(Exception):
    pass


def requirement_lines(text: str) -> list[str]:
    """The requirement entries in a lockfile, without comments and blank lines."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def unpinned_requirements(lines: list[str]) -> list[str]:
    """Entries that do not pin an exact version with ==. Option lines are ignored."""
    return [line for line in lines if not line.startswith("-") and "==" not in line]


def render_dockerfile(env: Environment, *, has_requirements: bool) -> str:
    lines = [
        f"FROM {env.base_image}",
        "ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1",
    ]
    if env.system_packages:
        packages = " ".join(f"{package.name}={package.version}" for package in env.system_packages)
        lines += [
            "RUN apt-get update \\",
            f" && apt-get install -y --no-install-recommends {packages} \\",
            " && rm -rf /var/lib/apt/lists/*",
        ]
    cache = env.dependencies.cache
    if has_requirements:
        lines.append("COPY requirements.lock /tmp/requirements.lock")
        if cache:
            lines.append(f"COPY wheels/ {cache.path}/")
            lines.append(f"RUN pip install --no-index --find-links {cache.path} -r /tmp/requirements.lock")
        else:
            lines.append("RUN pip install -r /tmp/requirements.lock")
    lines.append("RUN pip install " + " ".join(PLATFORM_PIP_PACKAGES))
    lines += [
        f"RUN useradd --create-home --uid {CANDIDATE_UID} {CANDIDATE_USER}",
        f"USER {CANDIDATE_USER}",
        "WORKDIR /workspace",
    ]
    return "\n".join(lines) + "\n"


def build_context(package: Package, dest: Path) -> Path:
    """Write the files a build needs into `dest` and return it."""
    if package.meta.language != "python":
        raise ImageError(f"the image builder only supports python so far, not {package.meta.language}")
    env = package.meta.environment
    lock_path = package.root / env.dependencies.lockfile
    if not lock_path.is_file():
        raise ImageError(f"lockfile {env.dependencies.lockfile} is missing")
    lock_text = lock_path.read_bytes().decode("utf-8")
    has_requirements = bool(requirement_lines(lock_text))

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "Dockerfile").write_bytes(render_dockerfile(env, has_requirements=has_requirements).encode("utf-8"))
    if has_requirements:
        (dest / "requirements.lock").write_bytes(lock_text.encode("utf-8"))
        cache = env.dependencies.cache
        if cache:
            wheels = package.root / WHEELS_DIR
            if not wheels.is_dir():
                raise ImageError(f"the package asks for a dependency cache but {WHEELS_DIR} is missing")
            if tree_sha256(wheels) != cache.sha256:
                raise ImageError(f"{WHEELS_DIR} does not match the hash recorded in package.yaml")
            shutil.copytree(wheels, dest / "wheels")
    return dest


def image_tag(context: Path) -> str:
    """A tag taken from the content of the build context."""
    return f"gassess-env:{tree_sha256(context)[:12]}"


def ensure_image(package: Package, work: Path) -> str:
    """Build the package's image if it does not exist yet, and return its tag."""
    context = build_context(package, work / "image-context")
    tag = image_tag(context)
    try:
        if not image_exists(tag):
            build_image(context, tag)
    except DockerError as exc:
        raise ImageError(str(exc)) from exc
    return tag
