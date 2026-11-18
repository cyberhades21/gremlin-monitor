"""Prove a package version is sound before a candidate sees it. See docs/specs/06."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from gremlin_assess import __version__
from gremlin_assess.brief import check_brief, disclosed_counts
from gremlin_assess.container import DockerError, Mount, docker_available, open_permissions, run_container
from gremlin_assess.export import ExportError, commit_count, export_public
from gremlin_assess.grading import GradeResult, grade
from gremlin_assess.grading.container_runner import container_runner
from gremlin_assess.grading.junit import JUnitError, parse_junit
from gremlin_assess.grading.rigs import NO_FLAG_EXPECTED, rig_set
from gremlin_assess.hashing import content_hash, file_sha256
from gremlin_assess.image import ImageError, ensure_image, requirement_lines, unpinned_requirements
from gremlin_assess.leakscan import scan_public
from gremlin_assess.package import Package, PackageError, load_package, split_test_ref
from gremlin_assess.patches import PatchError, apply_patches
from workspace_tools.findings_check import check_findings_text

Grader = Callable[[Path], GradeResult]

CHECKS = {
    "V1": "Structure",
    "V2": "Offline build",
    "V3": "Flawed state",
    "V4": "Reference state",
    "V5": "Item isolation",
    "V6": "Grading integrity",
    "V7": "Agent alone",
    "V8": "Unlisted-flaw audit",
    "V9": "Truth is findable",
    "V10": "Licence",
    "V11": "Leak scan",
    "V12": "Hours log",
}
# Checks that need pieces which do not exist yet. They are reported as not run,
# never as passed.
NOT_BUILT_YET = {
    "V2": "pass --containers to run it. It needs Docker, and a network when the image is built",
    "V7": "needs the pinned agent and a model key",
    "V8": "needs the pinned agent and a model key",
}
STANDARD_NOTICE = "This copy has been modified from the original work."
HOUR_STAGES = ("flaw_design", "hidden_tests", "validation", "approval")
HOUR_COLUMNS = {"date", "person", "stage", "hours", "note"}
ASSERTION_PREFIXES = ("assert", "AssertionError", "Failed")


@dataclass
class CheckResult:
    id: str
    name: str
    status: str  # pass | fail | not_run
    problems: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    package_id: str
    version: str | None
    content_hash: str | None
    agent_config: str | None
    checks: list[CheckResult]
    expected_tests: list[str] = field(default_factory=list)

    @property
    def validated(self) -> bool:
        """True only when every check ran and passed."""
        return all(check.status == "pass" for check in self.checks)

    def check(self, check_id: str) -> CheckResult:
        return next(check for check in self.checks if check.id == check_id)

    def comparable(self) -> dict:
        """Everything that must be identical between two runs on the same version."""
        return {
            "package": {"id": self.package_id, "version": self.version, "content_hash": self.content_hash},
            "validator_version": __version__,
            "agent_config": self.agent_config,
            "checks": [asdict(check) for check in self.checks],
            "expected_tests": self.expected_tests,
            "validated": self.validated,
        }

    def to_dict(self) -> dict:
        return {**self.comparable(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def write_report(report: ValidationReport, package_root: Path) -> Path:
    path = Path(package_root) / "validation" / "report.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


# --- helpers ----------------------------------------------------------------


def _load_acknowledgements(package: Package) -> dict:
    path = package.validation_dir / "acknowledgements.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _patch_order(package: Package, item_ids: Iterable[str]) -> list[str]:
    """Item ids with every dependency placed before the item that needs it."""
    ordered: list[str] = []

    def visit(item_id: str, trail: tuple[str, ...]) -> None:
        if item_id in ordered:
            return
        if item_id in trail:
            raise PatchError(f"{item_id}: depends_on forms a loop")
        for dependency in package.item(item_id).depends_on:
            visit(dependency, (*trail, item_id))
        ordered.append(item_id)

    for item_id in item_ids:
        visit(item_id, ())
    return ordered


def _copy_repo(package: Package, dest: Path, item_ids: Iterable[str]) -> Path:
    """Copy the flawed repo to `dest` and apply the reference patches of `item_ids`."""
    shutil.copytree(package.repo_dir, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    patches = [package.patch_for(i) for i in _patch_order(package, item_ids) if package.patch_for(i).is_file()]
    apply_patches(dest, patches)
    return dest


def _describe(result: GradeResult) -> str:
    return "; ".join(flag.describe() for flag in result.flags) or "no flags"


# --- checks -----------------------------------------------------------------


def _v1(package: Package) -> list[str]:
    problems: list[str] = []
    ids = [item.id for item in package.checklist]

    for item_id in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{item_id}: id is used more than once")
    causes = [item.root_cause_id for item in package.gremlins()]
    for cause in sorted({c for c in causes if causes.count(c) > 1}):
        problems.append(f"{cause}: root_cause_id is used by more than one item")
    extra_ids = [extra.id for extra in package.extras]
    for extra_id in sorted({i for i in extra_ids if extra_ids.count(i) > 1}):
        problems.append(f"{extra_id}: extras id is used more than once")

    rubric = {criterion.id: criterion for criterion in package.rubric}
    for criterion in package.rubric:
        if criterion.item not in ids:
            problems.append(f"{criterion.id}: points at item {criterion.item}, which is not in the checklist")

    for item in package.checklist:
        for dependency in item.depends_on:
            if dependency not in ids or dependency == item.id:
                problems.append(f"{item.id}: depends_on names {dependency}, which is not another item")
        for ref in item.tests:
            file, _ = split_test_ref(ref)
            if not (package.tests_dir / file).is_file():
                problems.append(f"{item.id}: hidden test file {file} is missing")
        if item.grading == "rubric":
            criterion = rubric.get(item.rubric_ref or "")
            if criterion is None:
                problems.append(f"{item.id}: rubric_ref {item.rubric_ref} is not in rubric.yaml")
            elif criterion.item != item.id:
                problems.append(f"{item.id}: rubric_ref {item.rubric_ref} belongs to {criterion.item}")
        if (item.kind == "gremlin" or item.grading == "test") and not package.patch_for(item.id).is_file():
            problems.append(f"{item.id}: reference patch hidden/reference/{item.id}.patch is missing")

    if not any(package.tests_dir.glob("guards/test_*.py")):
        problems.append("hidden/tests/guards has no tests")
    if package.walkthrough is None:
        problems.append("walkthrough.yaml: file is missing")

    if not package.repo_dir.is_dir():
        problems.append("public/repo: directory is missing")
    lockfile = package.root / package.meta.environment.dependencies.lockfile
    if not lockfile.is_file():
        problems.append(f"package.yaml: lockfile {package.meta.environment.dependencies.lockfile} is missing")

    brief = package.public_dir / "BRIEF.md"
    if brief.is_file():
        problems.extend(check_brief(brief.read_text(encoding="utf-8"), disclosed_counts(package.checklist)))
    else:
        problems.append("public/BRIEF.md: file is missing")

    template = package.public_dir / "FINDINGS.yaml"
    if template.is_file():
        findings, template_problems = check_findings_text(template.read_text(encoding="utf-8"))
        if template_problems or findings:
            problems.append("public/FINDINGS.yaml: the template must parse and hold no findings")
    else:
        problems.append("public/FINDINGS.yaml: file is missing")
    return problems


def _v3(package: Package, flawed: GradeResult) -> list[str]:
    problems: list[str] = []
    if not flawed.integrity_ok:
        problems.append(f"the grading run on the flawed repo was not clean: {_describe(flawed)}")
        return problems
    for item in package.test_backed():
        outcomes = flawed.items[item.id].tests
        if not outcomes:
            problems.append(f"{item.id}: its hidden tests produced no results")
        elif item.kind == "gremlin":
            for test in outcomes:
                label = f"{item.id}: {test.file}::{test.name}"
                if test.outcome == "passed":
                    problems.append(f"{label} passes on the flawed repo, so it does not detect the flaw")
                elif test.outcome != "failed" or not test.message.lstrip().startswith(ASSERTION_PREFIXES):
                    problems.append(
                        f"{label} must fail by assertion, but it ended as {test.outcome}: {test.message[:80]}"
                    )
        elif all(test.outcome == "passed" for test in outcomes):
            problems.append(f"{item.id}: every test already passes on the flawed repo")
    for guard in flawed.regressions:
        problems.append(f"guard test {guard.file}::{guard.name} does not pass on the flawed repo")
    return problems


def _v4(package: Package, reference: GradeResult) -> list[str]:
    problems: list[str] = []
    if not reference.integrity_ok:
        problems.append(f"the grading run on the reference solution was not clean: {_describe(reference)}")
        return problems
    for item in package.test_backed():
        result = reference.items[item.id]
        if result.fixed == "yes":
            continue
        failing = [test for test in result.tests if test.outcome != "passed"]
        detail = f"{failing[0].file}::{failing[0].name} is {failing[0].outcome}" if failing else "no test results"
        problems.append(f"{item.id}: not fixed by the reference solution ({detail})")
    for guard in reference.regressions:
        problems.append(f"guard test {guard.file}::{guard.name} fails with the reference solution")
    # A correct candidate would trip the same flags, so the reference must be clean.
    for flag in reference.flags:
        problems.append(f"the reference solution raises a flag: {flag.describe()}")
    return problems


NETWORK_PROBE = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    "except OSError:\n"
    "    sys.exit(0)\n"
    "sys.exit(7)\n"
)
RESULTS_FILE = "junit.xml"


def _tail(text: str, lines: int = 8) -> str:
    return " | ".join(text.strip().splitlines()[-lines:])


def _v2(package: Package, tag: str, work: Path) -> list[str]:
    """Offline build: with the network off, the build and the public tests run."""
    env = package.meta.environment
    problems: list[str] = []
    lock = package.root / env.dependencies.lockfile
    for line in unpinned_requirements(requirement_lines(lock.read_text(encoding="utf-8"))):
        problems.append(f"{env.dependencies.lockfile}: '{line}' does not pin an exact version with ==")

    repo = work / "v2-repo"
    shutil.copytree(package.repo_dir, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    open_permissions(repo)
    target = f"/workspace/{package.meta.candidate_name}"
    options = {
        "mounts": [Mount(repo, target, read_only=False)],
        "workdir": target,
        "memory": env.resources.memory,
        "cpus": env.resources.cpu,
    }
    try:
        probe = run_container(tag, ["python", "-c", NETWORK_PROBE], timeout=60, **options)
        if probe.exit_code != 0:
            problems.append("the container could still reach the network")

        if env.build_command:
            build = run_container(tag, ["sh", "-c", env.build_command], timeout=600, **options)
            if build.timed_out:
                problems.append("the build command did not finish within 600 seconds with the network off")
            elif build.exit_code != 0:
                problems.append(
                    f"the build command failed with the network off (exit {build.exit_code}): {_tail(build.output)}"
                )

        tests = run_container(tag, ["sh", "-c", env.test_command], timeout=600, **options)
        if tests.timed_out:
            problems.append("the test command did not finish within 600 seconds with the network off")
        elif tests.exit_code not in (0, 1):
            problems.append(
                f"the test command ended with exit {tests.exit_code} and did not run to completion: "
                f"{_tail(tests.output)}"
            )
        else:
            results = repo / RESULTS_FILE
            if not results.is_file():
                problems.append(f"the test command wrote no {RESULTS_FILE} in the repo root")
            else:
                try:
                    if not parse_junit(results).cases:
                        problems.append(f"{RESULTS_FILE} holds no test results")
                except JUnitError as exc:
                    problems.append(f"{RESULTS_FILE}: {exc}")
    except DockerError as exc:
        problems.append(str(exc))
    return problems


def _v5(package: Package, flawed: GradeResult, work: Path, grader: Grader) -> list[str]:
    problems: list[str] = []
    for item in package.checklist:
        if not package.patch_for(item.id).is_file():
            continue
        try:
            applied = _patch_order(package, [item.id])
            repo = _copy_repo(package, work / f"only-{item.id}", [item.id])
        except PatchError as exc:
            problems.append(f"{item.id}: {exc}")
            continue
        result = grader(repo)
        if not result.integrity_ok:
            problems.append(f"{item.id}: the grading run with only its patch was not clean: {_describe(result)}")
            continue
        if item.grading == "test" and result.items[item.id].fixed != "yes":
            problems.append(f"{item.id}: its own patch does not make its tests pass")
        for other in package.test_backed():
            if other.id in applied:
                continue
            before, after = flawed.items[other.id].fixed, result.items[other.id].fixed
            if before != after:
                problems.append(f"{item.id}: its patch changes the result of {other.id} from {before} to {after}")
        for guard in result.regressions:
            problems.append(f"{item.id}: its patch breaks guard test {guard.file}::{guard.name}")
    return problems


def _v6(
    package: Package,
    flawed: GradeResult,
    reference: GradeResult | None,
    work: Path,
    grader: Grader,
    container: bool,
) -> list[str]:
    problems: list[str] = []
    if not flawed.integrity_ok:
        problems.append("the integrity checks do not pass on the flawed repo")
    if reference is None:
        problems.append("the reference solution could not be built, so its integrity checks did not run")
    elif not reference.integrity_ok:
        problems.append("the integrity checks do not pass on the reference solution")

    test_backed_gremlins = [item.id for item in package.gremlins() if item.grading == "test"]
    for name, apply_rig in rig_set(container).items():
        repo = work / f"rig-{name}"
        shutil.copytree(package.repo_dir, repo)
        apply_rig(repo)
        result = grader(repo)
        wrongly_fixed = [item_id for item_id in test_backed_gremlins if result.items[item_id].fixed == "yes"]
        if wrongly_fixed:
            problems.append(f"rig '{name}' made {', '.join(wrongly_fixed)} look fixed")
        elif not result.flags and name not in NO_FLAG_EXPECTED:
            problems.append(f"rig '{name}' raised no flag")
    return problems


def _evidence_exists(repo: Path, entry: str) -> bool:
    if "::" in entry:
        name, _, symbol = entry.partition("::")
        path = repo / name
        return path.is_file() and symbol in path.read_text(encoding="utf-8", errors="ignore")
    name, _, line = entry.partition("#L")
    path = repo / name
    if not path.is_file():
        return False
    if not line:
        return True
    return line.isdigit() and 1 <= int(line) <= len(path.read_text(encoding="utf-8", errors="ignore").split("\n"))


def _v9(package: Package, acknowledgements: dict) -> list[str]:
    problems: list[str] = []
    confirmed = set(acknowledgements.get("truth_confirmed") or [])
    for item in package.gremlins():
        for entry in item.truth_evidence:
            if not _evidence_exists(package.repo_dir, entry):
                problems.append(f"{item.id}: truth_evidence '{entry}' does not exist in the public repo")
        if item.contradiction and item.id not in confirmed:
            problems.append(f"{item.id}: the engineer has not confirmed its truth evidence in acknowledgements.yaml")
    return problems


def _v10(package: Package) -> list[str]:
    problems: list[str] = []
    upstream = package.meta.upstream
    licence_file = package.root / upstream.licence_file
    if not licence_file.is_file():
        problems.append(f"licence file {upstream.licence_file} is missing")
    elif not licence_file.is_relative_to(package.repo_dir):
        problems.append("the licence file must ship inside public/repo")
    elif file_sha256(licence_file) != upstream.licence_file_sha256:
        problems.append("the licence file does not match its recorded hash")

    notice = package.repo_dir / "NOTICE"
    if not notice.is_file():
        problems.append("public/repo/NOTICE is missing")
    elif notice.read_text(encoding="utf-8").strip() != STANDARD_NOTICE:
        problems.append("public/repo/NOTICE must hold exactly the standard sentence")
    return problems


def _v11(package: Package, work: Path) -> list[str]:
    internal_ids = [package.meta.id]
    for item in package.checklist:
        internal_ids += [item.id, item.root_cause_id or ""]
    internal_ids += [extra.id for extra in package.extras] + [criterion.id for criterion in package.rubric]

    problems = [
        f"{leak.path}" + (f":{leak.line}" if leak.line else "") + f": {leak.reason}"
        for leak in scan_public(package.public_dir, internal_ids)
    ]
    try:
        exported = export_public(package, work / "export")
        commits = commit_count(exported)
        if commits != 1:
            problems.append(f"the exported repo has {commits} commits and must have exactly 1")
    except ExportError as exc:
        problems.append(f"the candidate workspace could not be exported: {exc}")
    return problems


def _v12(package: Package) -> list[str]:
    path = package.validation_dir / "hours.csv"
    if not path.is_file():
        return ["validation/hours.csv is missing"]
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != HOUR_COLUMNS:
            return [f"validation/hours.csv must have the columns {', '.join(sorted(HOUR_COLUMNS))}"]
        rows = list(reader)

    problems: list[str] = []
    logged = set()
    for number, row in enumerate(rows, start=2):
        try:
            hours = float(row["hours"])
        except (TypeError, ValueError):
            hours = 0.0
        if hours <= 0:
            problems.append(f"validation/hours.csv line {number}: hours must be a number above zero")
        if row["stage"] not in (*HOUR_STAGES, "other"):
            problems.append(f"validation/hours.csv line {number}: unknown stage '{row['stage']}'")
        logged.add(row["stage"])
    for stage in HOUR_STAGES:
        if stage not in logged:
            problems.append(f"validation/hours.csv has no entry for stage '{stage}'")
    return problems


# --- entry point ------------------------------------------------------------


def validate(
    root: Path,
    *,
    python: str | None = None,
    checks: Iterable[str] | None = None,
    containers: bool = False,
) -> ValidationReport:
    """Run the validator on the package at `root`.

    `python` is the interpreter that has the package's dependencies and pytest.
    `checks` limits the run to some check ids. The rest are reported as not run.
    `containers` builds the package image and runs V2 and the graded checks in
    it. Without Docker these report not_run, never a quiet fallback to local runs.
    """
    root = Path(root)
    wanted = set(CHECKS) if checks is None else set(checks)
    results: dict[str, CheckResult] = {}

    def record(check_id: str, problems: list[str]) -> None:
        results[check_id] = CheckResult(check_id, CHECKS[check_id], "fail" if problems else "pass", problems)

    def skip(check_id: str, reason: str) -> None:
        results[check_id] = CheckResult(check_id, CHECKS[check_id], "not_run", [reason])

    try:
        package = load_package(root)
    except PackageError as exc:
        record("V1", exc.problems)
        for check_id in CHECKS:
            if check_id != "V1":
                skip(check_id, "the package files did not load, so fix V1 first")
        return ValidationReport(root.name, None, None, None, [results[c] for c in CHECKS])

    expected_tests: list[str] = []
    acknowledgements = _load_acknowledgements(package)
    record("V1", _v1(package))

    with tempfile.TemporaryDirectory(prefix="validate-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        graded = wanted & {"V3", "V4", "V5", "V6"}
        needs_image = containers and results["V1"].status == "pass" and bool(wanted & ({"V2"} | graded))

        tag: str | None = None
        runner = None
        container_problem: str | None = None  # why container checks cannot run, when they cannot
        if needs_image:
            if not docker_available():
                container_problem = "Docker is not running"
            else:
                try:
                    tag = ensure_image(package, work)
                    resources = package.meta.environment.resources
                    runner = container_runner(tag, memory=resources.memory, cpus=resources.cpu)
                except (ImageError, DockerError) as exc:
                    container_problem = str(exc)

        if "V2" in wanted:
            if not containers:
                skip("V2", NOT_BUILT_YET["V2"])
            elif results["V1"].status != "pass":
                skip("V2", "fix V1 first")
            elif tag is None:
                if container_problem == "Docker is not running":
                    skip("V2", container_problem)
                else:
                    record("V2", [container_problem or "the image could not be built"])
            else:
                record("V2", _v2(package, tag, work))

        def grader(repo: Path) -> GradeResult:
            return grade(package, repo, python=python, runner=runner)

        if graded and results["V1"].status != "pass":
            for check_id in graded:
                skip(check_id, "fix V1 first")
        elif graded and containers and runner is None:
            for check_id in graded:
                skip(check_id, f"the container run is not available: {container_problem}")
        elif graded:
            flawed = grader(package.repo_dir)
            if "V3" in wanted:
                record("V3", _v3(package, flawed))
            reference = None
            if wanted & {"V4", "V6"}:
                try:
                    reference_repo = _copy_repo(package, work / "reference", [i.id for i in package.checklist])
                    reference = grader(reference_repo)
                    reference_problems = _v4(package, reference)
                    if not reference_problems:
                        expected_tests = reference.seen_tests
                except PatchError as exc:
                    reference_problems = [str(exc)]
                if "V4" in wanted:
                    record("V4", reference_problems)
            if "V5" in wanted:
                record("V5", _v5(package, flawed, work, grader))
            if "V6" in wanted:
                record("V6", _v6(package, flawed, reference, work, grader, container=runner is not None))

        if "V9" in wanted:
            record("V9", _v9(package, acknowledgements))
        if "V10" in wanted:
            record("V10", _v10(package))
        if "V11" in wanted:
            record("V11", _v11(package, work))
        if "V12" in wanted:
            record("V12", _v12(package))

    for check_id in CHECKS:
        if check_id not in results:
            skip(check_id, NOT_BUILT_YET.get(check_id, "not selected for this run"))

    return ValidationReport(
        package_id=package.meta.id,
        version=package.meta.version,
        content_hash=content_hash(root),
        agent_config=package.meta.agent_config,
        checks=[results[check_id] for check_id in CHECKS],
        expected_tests=expected_tests,
    )
