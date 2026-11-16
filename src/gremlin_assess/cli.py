"""Command line for the pilot tooling. Run `gassess --help`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gremlin_assess.brief import disclosed_counts, render_counts_table
from gremlin_assess.export import ExportError, export_public
from gremlin_assess.grading import grade
from gremlin_assess.matching import match_findings
from gremlin_assess.package import PackageError, load_package
from gremlin_assess.patches import PatchError, make_patch
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


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate(args.package, python=args.python, checks=args.only)
    for check in report.checks:
        print(f"{check.id:<4} {check.name:<22} {check.status}")
        for problem in check.problems:
            print(f"       - {problem}")
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
    print("Validated.")
    return 0


def _cmd_grade(args: argparse.Namespace) -> int:
    package = _load(args.package)
    if package is None:
        return 1
    # The list of tests recorded from the trusted reference run, when there is one.
    expected = None
    report_path = package.validation_dir / "report.json"
    if report_path.is_file():
        expected = json.loads(report_path.read_text(encoding="utf-8")).get("expected_tests") or None

    result = grade(package, args.repo, python=args.python, expected_tests=expected)
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
    for match in match_findings(findings, package.checklist, package.extras):
        target = match.item_id or match.extra_id or "-"
        note = " (category differs)" if match.category_mismatch else ""
        print(f"{match.finding_index:>3}  {match.outcome:<14} {target:<7} {titles[match.finding_index]}{note}")
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
    command.set_defaults(run=_cmd_validate)

    command = commands.add_parser("grade", help="run hidden tests against a final repo")
    command.add_argument("package", type=Path)
    command.add_argument("repo", type=Path)
    command.add_argument("--python", help=python_help)
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

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
