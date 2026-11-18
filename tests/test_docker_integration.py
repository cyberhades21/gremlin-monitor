"""Tests that run real containers. They skip when Docker is not running.

The first run pulls the pinned base image and builds the toy image, which
needs the network. Later runs reuse both. Skip them with: pytest -m "not docker"
"""

import shutil
import subprocess

import pytest

from conftest import TOY
from gremlin_assess.agentchecks import grade_agent_alone, prepare_agent_alone
from gremlin_assess.container import Mount, docker_available, run_container
from gremlin_assess.grading import grade
from gremlin_assess.grading.container_runner import container_runner
from gremlin_assess.grading.rigs import CONTAINER_RIGS, RIGS
from gremlin_assess.image import ensure_image
from gremlin_assess.package import load_package
from gremlin_assess.patches import apply_patches
from gremlin_assess.validator import validate

pytestmark = pytest.mark.docker


@pytest.fixture(scope="session")
def image(tmp_path_factory):
    if not docker_available():
        pytest.skip("Docker is not running")
    return ensure_image(load_package(TOY), tmp_path_factory.mktemp("image"))


@pytest.fixture
def runner(image):
    return container_runner(image)


def fixed(result):
    return {item_id: item.fixed for item_id, item in result.items.items() if item.fixed != "not_graded"}


def test_the_container_has_no_network(image, tmp_path):
    probe = (
        "import socket, sys\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "except OSError:\n    sys.exit(0)\nsys.exit(7)\n"
    )
    assert run_container(image, ["python", "-c", probe], timeout=60).exit_code == 0


def test_the_container_cannot_write_to_a_read_only_mount(image, tmp_path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    code = "open('/data/file.txt', 'w')"
    run = run_container(image, ["python", "-c", code], timeout=60, mounts=[Mount(tmp_path, "/data")])

    assert run.exit_code == 1 and "Read-only file system" in run.output


def test_a_hung_container_is_killed(image):
    run = run_container(image, ["sleep", "60"], timeout=3)

    assert run.timed_out
    listed = subprocess.run(["docker", "ps", "-q", "--filter", "name=gassess-"], capture_output=True, text=True)
    assert listed.stdout.strip() == ""


def test_container_grading_matches_local_grading(toy, runner):
    in_container = grade(toy, toy.repo_dir, runner=runner)
    locally = grade(toy, toy.repo_dir)

    assert in_container.integrity_ok and in_container.flags == []
    assert fixed(in_container) == fixed(locally) == {"G-001": "no", "G-003": "no", "F-001": "no"}
    assert len(in_container.guards) == 5


@pytest.mark.parametrize("name", [*RIGS, *CONTAINER_RIGS])
def test_no_rig_fools_the_container_runner(toy, runner, tmp_path, name):
    repo = tmp_path / "repo"
    shutil.copytree(toy.repo_dir, repo)
    {**RIGS, **CONTAINER_RIGS}[name](repo)

    result = grade(toy, repo, runner=runner)

    assert "yes" not in fixed(result).values()


def test_an_agent_alone_run_is_graded_in_the_container(toy, runner, tmp_path):
    # The run's repo is a git checkout, like a candidate's, and goes into the container read-only.
    dest = prepare_agent_alone(toy, tmp_path / "runs", runs=1)
    run = dest / "run-1"
    apply_patches(run / "workspace" / "tallybook", [toy.patch_for("G-001")])
    (run / "run.yaml").write_text("agent_config: ac-test\nended_by: agent_stopped\n", encoding="utf-8")

    table = grade_agent_alone(toy, dest, mode="container", runner=runner)

    entry = table["runs"][0]
    assert entry["problems"] == [] and entry["integrity_ok"]
    assert (entry["items"]["G-001"]["fixed"], entry["items"]["G-003"]["fixed"]) == ("yes", "no")


def test_the_validator_passes_v2_and_the_graded_checks_in_containers(image):
    report = validate(TOY, containers=True)

    assert {check.id: check.status for check in report.checks} == {
        "V1": "pass", "V2": "pass", "V3": "pass", "V4": "pass", "V5": "pass", "V6": "pass",
        "V7": "not_run", "V8": "not_run", "V9": "pass", "V10": "pass", "V11": "pass", "V12": "pass",
    }
    assert len(report.expected_tests) == 9
