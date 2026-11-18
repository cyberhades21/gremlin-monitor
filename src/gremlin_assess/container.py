"""Run things in Docker. The docker command line is the only dependency.

Every container started here has no network, no extra privileges and a
read-only root file system unless a caller asks otherwise. See docs/specs/08.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class DockerError(Exception):
    """Docker itself failed, as opposed to the command inside the container failing."""


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool = True


@dataclass(frozen=True)
class ContainerRun:
    exit_code: int | None
    timed_out: bool
    output: str


def docker_available(timeout: int = 20) -> bool:
    """True when the docker command exists and its daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def run_args(
    image: str,
    command: Sequence[str],
    *,
    mounts: Sequence[Mount] = (),
    workdir: str | None = None,
    network: str = "none",
    memory: str = "2g",
    cpus: float = 1.0,
    pids: int = 512,
    env: Mapping[str, str] | None = None,
    name: str | None = None,
) -> list[str]:
    """The full `docker run` command line. Pure, so it can be tested without Docker."""
    args = [
        "docker", "run", "--rm",
        "--network", network,
        "--memory", memory,
        "--cpus", str(cpus),
        "--pids-limit", str(pids),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=256m",
    ]
    if name:
        args += ["--name", name]
    for mount in mounts:
        if "," in str(mount.source) or "," in mount.target:
            raise DockerError(f"a mount path contains a comma, which Docker cannot take: {mount.source}")
        spec = f"type=bind,source={mount.source},target={mount.target}"
        args += ["--mount", spec + (",readonly" if mount.read_only else "")]
    if workdir:
        args += ["--workdir", workdir]
    for key, value in sorted((env or {}).items()):
        args += ["--env", f"{key}={value}"]
    return [*args, image, *command]


def open_permissions(root: Path) -> None:
    """Let the container's non-root user read and write a directory made by this process.

    Best effort. It does nothing useful on Windows, where Docker Desktop handles access.
    """
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(0o777 if path.is_dir() else 0o666)
        except OSError:
            pass


def run_container(image: str, command: Sequence[str], *, timeout: int, **options) -> ContainerRun:
    """Run a command in a fresh container. A hung container is killed when the time is up."""
    name = f"gassess-{secrets.token_hex(6)}"
    args = run_args(image, command, name=name, **options)
    try:
        completed = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        # Stopping the docker client does not stop the container.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        return ContainerRun(None, True, "")
    except FileNotFoundError as exc:
        raise DockerError("docker was not found on this machine") from exc
    output = completed.stdout + completed.stderr
    if completed.returncode == 125:  # Docker's own failure code
        raise DockerError(f"docker could not start the container: {output.strip()[-500:]}")
    return ContainerRun(completed.returncode, False, output)


def image_exists(tag: str) -> bool:
    result = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, timeout=60)
    return result.returncode == 0


def build_image(context: Path, tag: str, *, timeout: int = 900) -> None:
    """Build an image. The build itself may use the network, to fetch pinned inputs."""
    try:
        result = subprocess.run(
            ["docker", "build", "--tag", tag, str(context)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=dict(os.environ, DOCKER_BUILDKIT="1"),
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"the image build did not finish within {timeout} seconds") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
        raise DockerError(f"the image build failed:\n{tail}")
