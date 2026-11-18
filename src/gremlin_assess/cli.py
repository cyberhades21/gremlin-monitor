"""Command line for the pilot tooling. Run `gassess --help`."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

from gremlin_assess.agentchecks import (
    AGENT_ALONE_PROMPT,
    AGENT_ALONE_RUNS,
    AUDIT_PROMPT,
    AUDIT_RUNS,
    AgentCheckError,
    collect_audit,
    evaluate_agent_alone,
    evaluate_audit,
    grade_agent_alone,
    prepare_agent_alone,
    prepare_audit,
    write_agent_alone,
    write_audit,
)
from gremlin_assess.analysis import analyze_pilot
from gremlin_assess.brief import disclosed_counts, render_counts_table
from gremlin_assess.container import docker_available
from gremlin_assess.export import ExportError, export_public
from gremlin_assess.grading import grade
from gremlin_assess.grading.container_runner import container_runner
from gremlin_assess.image import ImageError, ensure_image
from gremlin_assess.matching import match_findings
from gremlin_assess.package import Package, PackageError, load_acknowledgements, load_package
from gremlin_assess.packet import PacketError, build_packet
from gremlin_assess.patches import PatchError, make_patch
from gremlin_assess.pipeline import grade_session
from gremlin_assess.report import ReportError
from gremlin_assess.reviews import ReviewError, load_step1, load_step2
from gremlin_assess.session import (
    SessionError,
    add_intervention,
    collect,
    missing_artifacts,
    new_session,
    session_path,
    snapshot,
    start_session,
    write_walkthrough,
)
from gremlin_assess.validator import validate, write_report
from workspace_tools.findings_check import check_findings_text


def _load(path: Path):
    try:
        return load_package(path)
    except PackageError as exc:
        print("The package could not be read:")
        for problem in exc.problems:
            print(f"  - {problem}")
        return None


def _expected_tests(package: Package) -> list[str] | None:
    """The tests seen in the trusted reference run, when the validator has recorded them."""
    report_path = package.validation_dir / "report.json"
    if report_path.is_file():
        return json.loads(report_path.read_text(encoding="utf-8")).get("expected_tests") or None
    return None


def _container_runner(package: Package, image: str | None):
    if not image:
        return None
    resources = package.meta.environment.resources
    return container_runner(image, memory=resources.memory, cpus=resources.cpu)


def _model_client(use_model: bool):
    """Returns (client, error). Both are None when no model was asked for."""
    if not use_model:
        return None, None
    from gremlin_assess.grading.model_client import AnthropicModelClient

    try:
        return AnthropicModelClient(), None
    except RuntimeError as exc:
        return None, f"Error: {exc}"


def _cmd_build_image(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    if not docker_available():
        print("Docker is not running. Start Docker Desktop and try again.")
        return 1
    with tempfile.TemporaryDirectory(prefix="image-", ignore_cleanup_errors=True) as work:
        try:
            print(ensure_image(package, Path(work)))
        except ImageError as exc:
            print(f"The image could not be built: {exc}")
            return 1
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate(args.package, python=args.python, checks=args.only, containers=args.containers)
    for check in report.checks:
        print(f"{check.id:<4} {check.name:<22} {check.status}")
        for problem in check.problems:
            print(f"       - {problem}")
        for warning in check.warnings:
            state = f"accepted: {warning.accepted}" if warning.accepted else "not accepted yet"
            print(f"       ! {warning.id} ({state})")
            print(f"         {warning.text}")
    if args.write:
        print(f"Report saved to {write_report(report, args.package)}")

    failed = [check.id for check in report.checks if check.status == "fail"]
    not_run = [check.id for check in report.checks if check.status == "not_run"]
    if failed:
        print(f"Not validated. Failed: {', '.join(failed)}")
        return 1
    if not_run:
        print(f"Not validated yet. Not run: {', '.join(not_run)}")
        return 0
    if report.unaccepted_warnings:
        print(
            "Not validated yet. Accept each warning with a reason under accepted_warnings in "
            "validation/acknowledgements.yaml: " + ", ".join(w.id for w in report.unaccepted_warnings)
        )
        return 0
    print("Validated.")
    return 0


def _cmd_grade(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    result = grade(
        package,
        args.repo,
        python=args.python,
        runner=_container_runner(package, args.image),
        expected_tests=_expected_tests(package),
    )
    for item_id, item in sorted(result.items.items()):
        print(f"{item_id:<7} {item.fixed}")
    for guard in result.regressions:
        print(f"regression: {guard.file}::{guard.name} is {guard.outcome}")
    for flag in result.flags:
        print(f"flag: {flag.describe()}")
    if not result.integrity_ok:
        print("The run could not be trusted. Every test-backed result needs review.")
    if args.out:
        args.out.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Results saved to {args.out}")
    return 0


def _cmd_counts(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    print(render_counts_table(disclosed_counts(package.checklist)), end="")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    try:
        repo = export_public(package, args.dest)
    except ExportError as exc:
        print(f"Export failed: {exc}")
        return 1
    print(f"Workspace created at {args.dest}, with the repo at {repo}")
    return 0


def _cmd_make_patch(args: argparse.Namespace) -> int:
    try:
        patch = make_patch(args.base, args.changed)
    except PatchError as exc:
        print(f"Could not make the patch: {exc}", file=sys.stderr)
        return 1
    if not patch:
        print("The two trees are identical, so there is nothing to write.", file=sys.stderr)
        return 1
    # Written as bytes so the line endings stay exactly as they are.
    if args.out:
        args.out.write_bytes(patch.encode("utf-8"))
    else:
        sys.stdout.buffer.write(patch.encode("utf-8"))
    return 0


def _cmd_check_findings(args: argparse.Namespace) -> int:
    findings, problems = check_findings_text(args.file.read_text(encoding="utf-8"))
    if problems:
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"The file is well formed and holds {len(findings)} findings.")
    return 0


def _cmd_match(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    findings, problems = check_findings_text(args.findings.read_text(encoding="utf-8"))
    if problems:
        print("The findings file is not well formed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    titles = {finding.index: finding.title for finding in findings}
    for match in match_findings(findings, package.checklist, package.extras, package.meta.candidate_name):
        target = match.item_id or match.extra_id or "-"
        note = " (category differs)" if match.category_mismatch else ""
        print(f"{match.finding_index:>3}  {match.outcome:<14} {target:<7} {titles[match.finding_index]}{note}")
    return 0


def _cmd_session(args: argparse.Namespace) -> int:
    try:
        if args.action == "new":
            package = _load(args.package)
            if package is None:
                return 1
            directory = new_session(
                args.pilot, args.session, package, args.candidate, agent_source=args.agent_source, key_id=args.key_id
            )
            print(f"Created {directory}")
        elif args.action == "start":
            start_session(args.pilot, args.session)
            print(f"{args.session} is running")
        elif args.action == "note":
            add_intervention(args.pilot, args.session, args.text)
            print("Noted.")
        elif args.action == "snapshot":
            directory = session_path(args.pilot, args.session)
            while True:
                print(f"Saved {snapshot(args.workspace, directory)}")
                if not args.every:
                    break
                time.sleep(args.every)
        elif args.action == "collect":
            problems = collect(args.pilot, args.session, args.submission)
            if problems:
                print("Collected, but the submission has problems:")
                for problem in problems:
                    print(f"  - {problem}")
                return 1
            print(f"Collected {args.session}")
        elif args.action == "walkthrough":
            print(f"Saved {write_walkthrough(args.pilot, args.session, args.result.replace('-', ' '), args.notes)}")
        elif args.action == "check":
            missing = missing_artifacts(session_path(args.pilot, args.session), args.stage)
            for name in missing:
                print(f"missing: {name}")
            print("Complete." if not missing else f"{len(missing)} missing for stage '{args.stage}'.")
            return 1 if missing else 0
    except SessionError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


def _cmd_grade_session(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    client, error = _model_client(args.model)
    if error:
        print(error)
        return 1
    try:
        report = grade_session(
            args.pilot,
            args.session,
            package,
            runner=_container_runner(package, args.image),
            python=args.python,
            client=client,
            expected_tests=_expected_tests(package),
        )
    except (SessionError, ReportError) as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Report written to {report}")
    if client is None:
        print("No model was used, so rubric items are not graded and open findings need a person.")
    return 0


def _print_evaluation(label: str, evaluation) -> int:
    print(f"{label} would be: {evaluation.status}")
    for problem in evaluation.problems:
        print(f"  - {problem}")
    for warning_id, text in evaluation.warnings:
        print(f"  ! {warning_id}: {text}")
    return 1 if evaluation.status == "fail" else 0


def _run_line(run: dict, package: Package) -> str:
    if run["problems"]:
        return f"{run['id']}: not graded. " + "; ".join(run["problems"])
    gremlins = package.gremlins()
    fixed = sum(1 for g in gremlins if run["items"][g.id]["fixed"] == "yes")
    unsure = sum(1 for g in gremlins if run["items"][g.id]["fixed"] in ("needs_review", "not_graded"))
    note = f", {unsure} not graded" if unsure else ""
    return (
        f"{run['id']}: fixed {fixed} of {len(gremlins)} gremlins{note}, "
        f"{run['findings']['false_positives']} false positives, {run['changed_files']} files changed"
    )


def _cmd_agent_alone(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    try:
        if args.action == "prepare":
            dest = prepare_agent_alone(package, args.dir, args.runs)
            print(f"Made {args.runs} candidate workspaces under {dest}.")
            print("In each run-N/workspace, start the pinned agent and give it exactly this prompt, with no follow-up:")
            print(f"  {AGENT_ALONE_PROMPT}")
            print("When a run ends, fill in run-N/run.yaml. Then run `gassess agent-alone grade`.")
            return 0
        client, error = _model_client(args.model)
        if error:
            print(error)
            return 1
        table = grade_agent_alone(
            package,
            args.dir,
            mode="container" if args.image else "local",
            runner=_container_runner(package, args.image),
            python=args.python,
            client=client,
            expected_tests=_expected_tests(package),
        )
        path = write_agent_alone(package, table)
    except (AgentCheckError, ExportError) as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Saved {path}")
    for run in table["runs"]:
        print(_run_line(run, package))
    if not args.image:
        print("Graded outside a container. V7 only counts container runs, so repeat with --image for the record.")
    if client is None and any(i.kind == "gremlin" and i.grading == "rubric" for i in package.checklist):
        print("No model was used, so rubric-backed gremlins are not graded. Add --model, or settle them in acknowledgements.yaml.")
    acknowledgements, _ = load_acknowledgements(package.root)
    return _print_evaluation("V7", evaluate_agent_alone(package, acknowledgements))


def _cmd_audit(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    try:
        if args.action == "prepare":
            dest = prepare_audit(package, args.dir, args.runs)
            print(f"Made {args.runs} workspaces of the reference state under {dest}.")
            print("In each run-N/workspace, start the pinned agent and give it exactly this prompt:")
            print(f"  {AUDIT_PROMPT}")
            print("When a run ends, fill in run-N/run.yaml. Then run `gassess audit collect`.")
            return 0
        table = collect_audit(package, args.dir)
        path = write_audit(package, table)
    except (AgentCheckError, ExportError) as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Saved {path}")
    for report in table["reports"]:
        where = report["path"] + (f":{report['start']}" if report["start"] is not None else "")
        near = f" (near {', '.join(report['near_items'])})" if report["near_items"] else ""
        runs = ",".join(str(n) for n in report["runs"])
        print(f"{report['id']}  {where}  runs {runs}{near}  {report['titles'][0]}")
    if not table["reports"]:
        print("The runs reported nothing.")
    acknowledgements, _ = load_acknowledgements(package.root)
    return _print_evaluation("V8", evaluate_audit(package, acknowledgements))


def _cmd_packet(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    try:
        packet_id = build_packet(args.pilot, args.session, package, args.reviewer, args.out, args.forbid)
    except (PacketError, SessionError) as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Packet {packet_id} written under {args.out / packet_id}")
    print("Send step1 now. Send step2 only after the step 1 form comes back.")
    return 0


def _cmd_review_check(args: argparse.Namespace) -> int:
    try:
        data = yaml.safe_load(args.file.read_text(encoding="utf-8")) or {}
        if "overall" in data:
            load_step1(args.file)
            print("Step 1 form is valid.")
        else:
            package = _load(args.package)
            if package is None:
                return 1
            load_step2(args.file, package)
            print("Step 2 form is valid.")
    except (ReviewError, OSError, yaml.YAMLError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        text = analyze_pilot(args.pilot, args.packages)
    except (SessionError, ReviewError, PackageError, KeyError) as exc:
        print(f"Error: {exc}")
        return 1
    if args.out:
        args.out.write_bytes(text.encode("utf-8"))
        print(f"Saved {args.out}")
    else:
        print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gassess", description="Tooling for the assessment pilot.")
    commands = parser.add_subparsers(dest="command", required=True)
    python_help = "interpreter that has the package's dependencies and pytest (default: this one)"

    command = commands.add_parser("validate", help="run the validator checks on a package")
    command.add_argument("package", type=Path)
    command.add_argument("--python", help=python_help)
    command.add_argument("--only", nargs="+", metavar="CHECK", help="run only these checks, for example V1 V3")
    command.add_argument("--write", action="store_true", help="save validation/report.json")
    command.add_argument("--containers", action="store_true", help="run V2 and the graded checks in Docker")
    command.set_defaults(run=_cmd_validate)

    command = commands.add_parser("build-image", help="build the container image a package asks for")
    command.add_argument("package", type=Path)
    command.set_defaults(run=_cmd_build_image)

    command = commands.add_parser("grade", help="run hidden tests against a final repo")
    command.add_argument("package", type=Path)
    command.add_argument("repo", type=Path)
    command.add_argument("--python", help=python_help)
    command.add_argument("--image", help="run the tests in this container image (see build-image)")
    command.add_argument("--out", type=Path, help="save the results as JSON")
    command.set_defaults(run=_cmd_grade)

    command = commands.add_parser("counts", help="print the counts table for BRIEF.md")
    command.add_argument("package", type=Path)
    command.set_defaults(run=_cmd_counts)

    command = commands.add_parser("export", help="build a candidate workspace")
    command.add_argument("package", type=Path)
    command.add_argument("dest", type=Path)
    command.set_defaults(run=_cmd_export)

    command = commands.add_parser("make-patch", help="write a reference patch from two trees")
    command.add_argument("base", type=Path)
    command.add_argument("changed", type=Path)
    command.add_argument("--out", type=Path, help="write to this file instead of standard output")
    command.set_defaults(run=_cmd_make_patch)

    command = commands.add_parser("check-findings", help="check a findings file's format")
    command.add_argument("file", type=Path)
    command.set_defaults(run=_cmd_check_findings)

    command = commands.add_parser("match", help="match findings to checklist items by location")
    command.add_argument("package", type=Path)
    command.add_argument("findings", type=Path)
    command.set_defaults(run=_cmd_match)

    command = commands.add_parser("agent-alone", help="V7: prepare agent-alone runs, then grade them")
    agent_actions = command.add_subparsers(dest="action", required=True)
    sub = agent_actions.add_parser("prepare", help="make the candidate workspaces for the runs")
    sub.add_argument("package", type=Path)
    sub.add_argument("dir", type=Path, help="a new folder for the runs")
    sub.add_argument("--runs", type=int, default=AGENT_ALONE_RUNS)
    sub = agent_actions.add_parser("grade", help="grade the finished runs and record the result in the package")
    sub.add_argument("package", type=Path)
    sub.add_argument("dir", type=Path, help="the folder made by prepare")
    sub.add_argument("--image", help="run the hidden tests in this container image. V7 only counts container runs")
    sub.add_argument("--python", help=python_help)
    sub.add_argument("--model", action="store_true", help="use the grading model for rubric-backed gremlins")
    command.set_defaults(run=_cmd_agent_alone)

    command = commands.add_parser("audit", help="V8: prepare unlisted-flaw audit runs, then collect them")
    audit_actions = command.add_subparsers(dest="action", required=True)
    sub = audit_actions.add_parser("prepare", help="make workspaces of the reference state for the runs")
    sub.add_argument("package", type=Path)
    sub.add_argument("dir", type=Path, help="a new folder for the runs")
    sub.add_argument("--runs", type=int, default=AUDIT_RUNS)
    sub = audit_actions.add_parser("collect", help="merge the runs' findings and record them in the package")
    sub.add_argument("package", type=Path)
    sub.add_argument("dir", type=Path, help="the folder made by prepare")
    command.set_defaults(run=_cmd_audit)

    command = commands.add_parser("session", help="create and manage session folders")
    actions = command.add_subparsers(dest="action", required=True)

    def session_action(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text)
        sub.add_argument("pilot", type=Path, help="the pilot directory, kept outside git")
        sub.add_argument("session", help="a session id like S-001")
        return sub

    sub = session_action("new", "create a session folder")
    sub.add_argument("package", type=Path)
    sub.add_argument("--candidate", required=True, help="candidate code like C-01")
    sub.add_argument("--agent-source", default="ours", choices=["ours", "own-claude-code", "own-codex"])
    sub.add_argument("--key-id", help="id of the API key made for this session")
    session_action("start", "mark the session as running and record the start time")
    sub = session_action("note", "record an intervention")
    sub.add_argument("text")
    sub = session_action("snapshot", "save a compressed copy of the live workspace")
    sub.add_argument("workspace", type=Path)
    sub.add_argument("--every", type=int, help="repeat every N seconds until stopped")
    sub = session_action("collect", "move a submission into the session folder")
    sub.add_argument("submission", type=Path, help="the archive from submit, or an unpacked workspace")
    sub = session_action("walkthrough", "record the walkthrough result")
    sub.add_argument("--result", required=True, choices=["consistent", "doubtful", "not-done"])
    sub.add_argument("--notes", default="")
    sub = session_action("check", "list artifacts still missing for a stage")
    sub.add_argument("--stage", default="complete", choices=["collected", "graded", "complete"])
    command.set_defaults(run=_cmd_session)

    command = commands.add_parser("grade-session", help="grade a collected session and write its report")
    command.add_argument("pilot", type=Path)
    command.add_argument("session")
    command.add_argument("package", type=Path)
    command.add_argument("--image", help="run the hidden tests in this container image")
    command.add_argument("--python", help=python_help)
    command.add_argument("--model", action="store_true", help="use the grading model (needs `uv sync --extra model` and ANTHROPIC_API_KEY)")
    command.set_defaults(run=_cmd_grade_session)

    command = commands.add_parser("packet", help="build an anonymised reviewer packet")
    command.add_argument("pilot", type=Path)
    command.add_argument("session")
    command.add_argument("package", type=Path)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--forbid", nargs="+", required=True, help="strings that identify the candidate: name, email, handles")
    command.set_defaults(run=_cmd_packet)

    command = commands.add_parser("review-check", help="check a reviewer form")
    command.add_argument("package", type=Path)
    command.add_argument("file", type=Path)
    command.set_defaults(run=_cmd_review_check)

    command = commands.add_parser("analyze", help="compute the Gate 1a measures from a pilot directory")
    command.add_argument("pilot", type=Path)
    command.add_argument("--packages", type=Path, required=True, help="a directory holding the package folders")
    command.add_argument("--out", type=Path)
    command.set_defaults(run=_cmd_analyze)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
