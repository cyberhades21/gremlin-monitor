"""Tests that need no Docker: command lines, Dockerfiles and build contexts."""

from pathlib import Path

import pytest

from gremlin_assess.container import DockerError, Mount, run_args
from gremlin_assess.image import (
    ImageError,
    build_context,
    image_tag,
    render_dockerfile,
    requirement_lines,
    unpinned_requirements,
)
from gremlin_assess.models import Environment
from gremlin_assess.package import PackageError, load_package


def test_run_args_lock_the_container_down():
    args = run_args(
        "img:tag",
        ["pytest", "-q"],
        mounts=[Mount(Path("/a"), "/work/a"), Mount(Path("/b"), "/work/b", read_only=False)],
        workdir="/work/a",
        name="n1",
        env={"B": "2", "A": "1"},
    )

    assert args[:3] == ["docker", "run", "--rm"]
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args and "--read-only" in args
    assert f"type=bind,source={Path('/a')},target=/work/a,readonly" in args
    assert f"type=bind,source={Path('/b')},target=/work/b" in args
    assert args[args.index("--name") + 1] == "n1"
    assert args[args.index("--workdir") + 1] == "/work/a"
    assert args[args.index("--env") + 1 : args.index("--env") + 4] == ["A=1", "--env", "B=2"]
    assert args[-3:] == ["img:tag", "pytest", "-q"]


def test_a_mount_path_with_a_comma_is_refused():
    with pytest.raises(DockerError, match="comma"):
        run_args("img", ["true"], mounts=[Mount(Path("/a,b"), "/x")])


def test_toy_dockerfile(toy):
    dockerfile = render_dockerfile(toy.meta.environment, has_requirements=False)
    lines = dockerfile.splitlines()

    assert lines[0] == f"FROM {toy.meta.environment.base_image}"
    assert "apt-get" not in dockerfile and "requirements.lock" not in dockerfile
    assert "RUN pip install pytest==9.2.0" in lines
    assert lines[-3:] == ["RUN useradd --create-home --uid 1000 candidate", "USER candidate", "WORKDIR /workspace"]


def test_system_packages_and_a_dependency_cache():
    base = load_package(Path(__file__).parent / "fixtures" / "packages" / "toy").meta.environment
    env = Environment.model_validate(
        {
            **base.model_dump(),
            "system_packages": [{"name": "git", "version": "1:2.39.5"}, {"name": "make", "version": "4.3"}],
            "dependencies": {"lockfile": "x.lock", "cache": {"path": "/opt/wheels", "sha256": "0" * 64}},
        }
    )

    dockerfile = render_dockerfile(env, has_requirements=True)

    assert " && apt-get install -y --no-install-recommends git=1:2.39.5 make=4.3 \\" in dockerfile
    assert "COPY wheels/ /opt/wheels/" in dockerfile
    assert "RUN pip install --no-index --find-links /opt/wheels -r /tmp/requirements.lock" in dockerfile


def test_requirement_parsing():
    text = "# comment\n\nrequests==2.31.0 \\\n    --hash=sha256:abc\nflask>=2\n-r other.txt\n"
    lines = requirement_lines(text)

    assert lines[0] == "requests==2.31.0 \\"
    assert unpinned_requirements(lines) == ["flask>=2"]
    assert requirement_lines("# nothing here\n") == []


def test_build_context_and_tag(toy_copy, tmp_path):
    package = load_package(toy_copy)
    first = build_context(package, tmp_path / "one")
    assert sorted(path.name for path in first.iterdir()) == ["Dockerfile"]
    tag = image_tag(first)
    assert tag.startswith("gassess-env:") and len(tag) == len("gassess-env:") + 12
    assert image_tag(build_context(package, tmp_path / "two")) == tag

    lock = toy_copy / "public" / "repo" / "requirements.lock"
    lock.write_bytes(lock.read_bytes() + b"requests==2.31.0\n")
    second = build_context(load_package(toy_copy), tmp_path / "three")
    assert sorted(path.name for path in second.iterdir()) == ["Dockerfile", "requirements.lock"]
    assert "RUN pip install -r /tmp/requirements.lock" in (second / "Dockerfile").read_text(encoding="utf-8")
    assert image_tag(second) != tag


def test_a_dependency_cache_must_match_its_hash(toy_copy, tmp_path):
    lock = toy_copy / "public" / "repo" / "requirements.lock"
    lock.write_bytes(lock.read_bytes() + b"requests==2.31.0\n")
    meta = toy_copy / "package.yaml"
    cache = b'    cache: {path: /opt/wheels, sha256: "' + b"0" * 64 + b'"}'
    meta.write_bytes(meta.read_bytes().replace(b"    cache: null", cache))

    with pytest.raises(ImageError, match="environment/wheels is missing"):
        build_context(load_package(toy_copy), tmp_path / "a")

    (toy_copy / "environment" / "wheels").mkdir(parents=True)
    (toy_copy / "environment" / "wheels" / "requests-2.31.0-py3-none-any.whl").write_bytes(b"wheel")
    with pytest.raises(ImageError, match="does not match the hash"):
        build_context(load_package(toy_copy), tmp_path / "b")


def test_an_unquoted_numeric_hash_is_refused_with_advice(toy_copy):
    meta = toy_copy / "package.yaml"
    meta.write_bytes(meta.read_bytes().replace(b"    cache: null", b"    cache: {path: /opt/wheels, sha256: " + b"1" * 64 + b"}"))

    with pytest.raises(PackageError) as caught:
        load_package(toy_copy)

    assert caught.value.problems == [
        "package.yaml: environment.dependencies.cache.sha256: "
        "write the hash in quotes, because YAML reads some hashes as numbers"
    ]


def test_a_hash_must_be_64_hex_digits(toy_copy):
    meta = toy_copy / "package.yaml"
    text = meta.read_bytes().decode("utf-8").replace("licence_file_sha256: ", 'licence_file_sha256: "abc" #')
    meta.write_bytes(text.encode("utf-8"))

    with pytest.raises(PackageError) as caught:
        load_package(toy_copy)

    assert "licence_file_sha256" in caught.value.problems[0]


def test_only_python_images_are_supported(toy_copy, tmp_path):
    meta = toy_copy / "package.yaml"
    meta.write_bytes(meta.read_bytes().replace(b"language: python", b"language: java"))

    with pytest.raises(ImageError, match="only supports python"):
        build_context(load_package(toy_copy), tmp_path / "a")
