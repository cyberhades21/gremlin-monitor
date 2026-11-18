"""Gate 1a measures. See docs/specs/05, "Gate 1a measures".

With 8 to 12 sessions these figures are directional. Item agreement carries the
most weight, because it has about 100 data points.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import yaml

from gremlin_assess.agentchecks import AGENT_ALONE_RUNS, MAX_FULL_SOLVES, evaluate_agent_alone
from gremlin_assess.models import ChecklistItem
from gremlin_assess.package import Package, load_acknowledgements, load_package
from gremlin_assess.packet import load_assignments
from gremlin_assess.reviews import ReviewError, Step1, Step2, load_step1, load_step2
from gremlin_assess.session import load_meta

# Thresholds from docs/specs/05
ITEM_AGREEMENT_MIN = 0.90
CLOSENESS_MIN = -0.05
RUBRIC_PASS_AGREEMENT_MIN = 0.85
MAX_PASS_SPREAD = 1


@dataclass
class SessionData:
    session: str
    package_id: str
    agent_source: str
    our_fixed: dict[str, str]  # every checklist item: yes | no | needs_review | not_graded
    test_backed: set[str]
    rubric_scores: dict[str, list[int]]  # criterion -> the valid passes' scores
    rubric_median: dict[str, int | None]
    cost_usd: float | None


@dataclass
class ReviewData:
    session: str
    reviewer: str
    overall: int | None = None
    verdicts: dict[str, str] = field(default_factory=dict)
    rubric: dict[str, int] = field(default_factory=dict)
    process: dict[str, str] = field(default_factory=dict)
    minutes: float | None = None


@dataclass
class Measure:
    name: str
    value: float | None
    n: int
    threshold: str
    status: str  # met | not_met | reported | not_measured
    note: str = ""
    unit: str = "percent"  # percent | points | number | money | count

    def shown(self) -> str:
        if self.value is None:
            return "n/a"
        return {
            "percent": f"{self.value:.1%}",
            "points": f"{self.value * 100:+.1f} points",
            "number": f"{self.value:.2f}",
            "money": f"${self.value:.2f}",
            "count": f"{self.value:.0f}",
        }[self.unit]


@dataclass
class GateReport:
    measures: list[Measure]
    disagreements: list[tuple[str, str, str, str]]  # session, item, ours, reviewer's verdict
    sessions: int
    reviews: int


def _yes_or_no(verdict: str) -> str:
    return "yes" if verdict == "yes" else "no"  # 'partly' counts as no


def item_agreement(sessions: list[SessionData], reviews: list[ReviewData]) -> tuple[int, int, list[tuple[str, str, str, str]]]:
    """Returns (comparisons, agreements, disagreements) of our result with each reviewer's verdict."""
    by_session = {s.session: s for s in sessions}
    n = agree = 0
    disagreements = []
    for review in reviews:
        ours = by_session.get(review.session)
        if ours is None or not review.verdicts:
            continue
        for item in sorted(ours.test_backed):
            if ours.our_fixed.get(item) not in ("yes", "no") or item not in review.verdicts:
                continue
            n += 1
            if ours.our_fixed[item] == _yes_or_no(review.verdicts[item]):
                agree += 1
            else:
                disagreements.append((review.session, item, ours.our_fixed[item], review.verdicts[item]))
    return n, agree, disagreements


def reviewer_ceiling(sessions: list[SessionData], reviews: list[ReviewData]) -> tuple[int, int]:
    """Returns (comparisons, agreements) between pairs of reviewers on test-backed items."""
    by_session = {s.session: s for s in sessions}
    n = agree = 0
    for session_id, own in by_session.items():
        group = [r for r in reviews if r.session == session_id and r.verdicts]
        for first, second in combinations(group, 2):
            for item in sorted(own.test_backed):
                if item in first.verdicts and item in second.verdicts:
                    n += 1
                    agree += _yes_or_no(first.verdicts[item]) == _yes_or_no(second.verdicts[item])
    return n, agree


def rubric_pass_agreement(sessions: list[SessionData], passes: int = 3) -> tuple[int, int]:
    """Returns (criteria with all passes valid, those whose passes span at most 1 point)."""
    n = agree = 0
    for session in sessions:
        for scores in session.rubric_scores.values():
            if len(scores) == passes:
                n += 1
                agree += max(scores) - min(scores) <= MAX_PASS_SPREAD
    return n, agree


def grader_vs_reviewers(sessions: list[SessionData], reviews: list[ReviewData]) -> tuple[int, float | None, float | None]:
    """Returns (comparisons, mean absolute difference, share within 1 point) of the grader's median and the reviewers' mean."""
    differences = []
    for session in sessions:
        for criterion, median in session.rubric_median.items():
            scores = [r.rubric[criterion] for r in reviews if r.session == session.session and criterion in r.rubric]
            if median is not None and scores:
                differences.append(abs(median - statistics.mean(scores)))
    if not differences:
        return 0, None, None
    return len(differences), statistics.mean(differences), sum(d <= 1 for d in differences) / len(differences)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        for k in range(position, end + 1):
            ranks[order[k]] = (position + end) / 2 + 1
        position = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation. None when it cannot be computed, for example with fewer than 3 pairs or no variation."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mean_x, mean_y = statistics.mean(rx), statistics.mean(ry)
    spread_x = sum((a - mean_x) ** 2 for a in rx)
    spread_y = sum((b - mean_y) ** 2 for b in ry)
    if spread_x == 0 or spread_y == 0:
        return None
    return sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry)) / (spread_x * spread_y) ** 0.5


def overall_check(sessions: list[SessionData], overall: dict[str, list[int]]) -> tuple[int, float | None]:
    """Rank correlation of items fixed (ours) with the mean overall judgment, per session."""
    fixed, judged = [], []
    for session in sessions:
        if overall.get(session.session):
            fixed.append(sum(1 for v in session.our_fixed.values() if v == "yes"))
            judged.append(statistics.mean(overall[session.session]))
    return len(fixed), spearman(fixed, judged)


def _measure(name, rate, n, threshold, passed, unit="percent") -> Measure:
    if n == 0 or rate is None:
        return Measure(name, None, 0, threshold, "not_measured", "no data yet", unit)
    return Measure(name, rate, n, threshold, "met" if passed else "not_met", unit=unit)


def _agent_alone_measure(results: dict[str, tuple[int, int]] | None) -> Measure:
    """`results` maps a package id to (runs that fixed every scored gremlin, runs recorded)."""
    name = "Agent alone (full solves in 5 runs)"
    threshold = f"at most {MAX_FULL_SOLVES} of {AGENT_ALONE_RUNS} per package"
    if not results:
        return Measure(name, None, 0, threshold, "not_measured", "validator check V7 has not run", "count")
    worst = max(full for full, _ in results.values())
    note = "; ".join(f"{package_id}: {full} of {runs}" for package_id, (full, runs) in sorted(results.items()))
    return Measure(name, worst, len(results), threshold, "met" if worst <= MAX_FULL_SOLVES else "not_met", note, "count")


def compute_gate(
    sessions: list[SessionData],
    reviews: list[ReviewData],
    step1_overall: dict[str, list[int]],
    *,
    agent_alone: dict[str, tuple[int, int]] | None = None,
) -> GateReport:
    ours_sessions = [s for s in sessions if s.agent_source == "ours"]
    n, agree, disagreements = item_agreement(sessions, reviews)
    rate = agree / n if n else None
    ceiling_n, ceiling_agree = reviewer_ceiling(sessions, reviews)
    ceiling = ceiling_agree / ceiling_n if ceiling_n else None
    pass_n, pass_agree = rubric_pass_agreement(sessions)
    gv_n, gv_mean, gv_within = grader_vs_reviewers(sessions, reviews)
    corr_n, corr = overall_check(sessions, step1_overall)
    costs = [s.cost_usd for s in ours_sessions if s.cost_usd is not None]

    measures = [
        _measure("Item agreement (ours against reviewers)", rate, n, f"at least {ITEM_AGREEMENT_MIN:.0%}", rate is not None and rate >= ITEM_AGREEMENT_MIN),
        Measure("Reviewer ceiling (reviewers against each other)", ceiling, ceiling_n, "reported", "reported" if ceiling_n else "not_measured"),
        _measure(
            "Closeness (item agreement minus ceiling)",
            None if rate is None or ceiling is None else rate - ceiling,
            min(n, ceiling_n),
            f"no worse than {CLOSENESS_MIN * 100:.0f} points",
            rate is not None and ceiling is not None and rate - ceiling >= CLOSENESS_MIN,
            unit="points",
        ),
        _measure("Rubric pass agreement (passes within 1 point)", pass_agree / pass_n if pass_n else None, pass_n, f"at least {RUBRIC_PASS_AGREEMENT_MIN:.0%}", pass_n > 0 and pass_agree / pass_n >= RUBRIC_PASS_AGREEMENT_MIN),
        Measure("Grader against reviewers (share within 1 point)", gv_within, gv_n, "reported", "reported" if gv_n else "not_measured", f"mean absolute difference {gv_mean:.2f}" if gv_mean is not None else ""),
        Measure("Overall check (rank correlation, directional only)", corr, corr_n, "reported", "reported" if corr is not None else "not_measured", "needs at least 3 sessions with an overall judgment" if corr is None else "", "number"),
        _agent_alone_measure(agent_alone),
        Measure("Cost per session (our agent only)", statistics.mean(costs) if costs else None, len(costs), "reported", "reported" if costs else "not_measured", f"{len(sessions) - len(ours_sessions)} sessions used the candidate's own agent and are left out" if len(ours_sessions) != len(sessions) else "", "money"),
    ]
    return GateReport(measures, disagreements, len(sessions), len(reviews))


def render_gate(report: GateReport, hours: dict[str, dict[str, float]], fairness: tuple[int, float | None]) -> str:
    out = [
        "# Gate 1a measures",
        "",
        f"{report.sessions} sessions and {report.reviews} reviewer forms. These figures are directional, not statistical proof. Item agreement carries the most weight.",
        "",
        "Results were recorded with counts shown to candidates, which changes difficulty and is not measured here.",
        "",
        "| Measure | Value | Comparisons | Threshold | Status |",
        "|---|---|---|---|---|",
    ]
    for m in report.measures:
        note = f" {m.note}" if m.note else ""
        out.append(f"| {m.name} | {m.shown()} | {m.n} | {m.threshold} | {m.status.replace('_', ' ')}{note} |")

    out += ["", "## Hours per validated package", ""]
    if hours:
        for package_id, stages in sorted(hours.items()):
            parts = ", ".join(f"{stage} {value:g}" for stage, value in sorted(stages.items()))
            out.append(f"- {package_id}: {sum(stages.values()):g} hours ({parts})")
    else:
        out.append("No hours logs found.")

    out += ["", "## Candidate fairness survey", ""]
    n, mean = fairness
    out.append(f"{n} answers, mean fairness {mean:.1f} of 5." if n and mean is not None else "No survey answers found.")

    out += ["", "## Disagreements to label", "", "Label each as: test too strict, test too loose, reviewer error or unclear item.", ""]
    if report.disagreements:
        out += ["| Session | Item | Ours | Reviewer | Label |", "|---|---|---|---|---|"]
        out += [f"| {s} | {i} | {o} | {r} | |" for s, i, o, r in report.disagreements]
    else:
        out.append("None.")
    return "\n".join(out) + "\n"


# --- loading from a pilot directory -------------------------------------------


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_session_data(session_dir: Path, package: Package) -> SessionData:
    meta = load_meta(session_dir)
    grade = _read_json(session_dir / "grading" / "grade.json")
    if grade is None:
        raise ReviewError(f"{session_dir.name} has no grading/grade.json")
    rubric = _read_json(session_dir / "grading" / "rubric.json") or {"items": {}, "criteria": {}}
    our_fixed, test_backed = {}, set()
    for item in package.checklist:
        if item.grading == "test":
            our_fixed[item.id] = grade["items"][item.id]["fixed"]
            test_backed.add(item.id)
        else:
            our_fixed[item.id] = rubric["items"].get(item.id, "not_graded")
    return SessionData(
        session=meta.session,
        package_id=meta.package_id,
        agent_source=meta.agent_source,
        our_fixed=our_fixed,
        test_backed=test_backed,
        rubric_scores={cid: c["scores"] for cid, c in rubric["criteria"].items()},
        rubric_median={cid: c["median"] for cid, c in rubric["criteria"].items()},
        cost_usd=meta.cost_usd,
    )


def _load_reviews(pilot: Path, packages: dict[str, Package], sessions: dict[str, SessionData]) -> tuple[list[ReviewData], dict[str, list[int]]]:
    reviews_dir = pilot / "reviews"
    assignments = load_assignments(reviews_dir)
    merged: dict[tuple[str, str], ReviewData] = {}
    overall: dict[str, list[int]] = {}
    for path in sorted(reviews_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        packet = data.get("packet")
        if packet not in assignments:
            raise ReviewError(f"{path.name}: packet {packet} is not in assignments.csv")
        session_id, reviewer = assignments[packet]
        review = merged.setdefault((session_id, reviewer), ReviewData(session_id, reviewer))
        if "overall" in data:
            step1: Step1 = load_step1(path)
            review.overall = step1.overall
            overall.setdefault(session_id, []).append(step1.overall)
        elif "items" in data:
            package = packages[sessions[session_id].package_id]
            step2: Step2 = load_step2(path, package)
            review.verdicts = {i.id: i.verdict for i in step2.items}
            review.rubric = {r.id: r.score for r in step2.rubric}
            review.process = {p.id: p.rating for p in step2.process}
            review.minutes = step2.minutes
        else:
            raise ReviewError(f"{path.name}: this is neither a step 1 nor a step 2 form")
    return list(merged.values()), overall


def _hours(package_dirs: list[Path]) -> dict[str, dict[str, float]]:
    result = {}
    for directory in package_dirs:
        path = directory / "validation" / "hours.csv"
        if not path.is_file():
            continue
        stages: dict[str, float] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                stages[row["stage"]] = stages.get(row["stage"], 0.0) + float(row["hours"])
        result[directory.name] = stages
    return result


def _fairness(pilot: Path) -> tuple[int, float | None]:
    path = pilot / "survey.csv"
    if not path.is_file():
        return 0, None
    with path.open(newline="", encoding="utf-8") as handle:
        scores = [float(row["fair"]) for row in csv.DictReader(handle) if row.get("fair")]
    return len(scores), statistics.mean(scores) if scores else None


def analyze_pilot(pilot: Path, packages_dir: Path) -> str:
    """Read a pilot directory and return the Gate 1a report as Markdown."""
    package_dirs = sorted(p for p in Path(packages_dir).iterdir() if (p / "package.yaml").is_file())
    packages = {}
    for directory in package_dirs:
        package = load_package(directory)
        packages[package.meta.id] = package

    sessions: dict[str, SessionData] = {}
    for directory in sorted((Path(pilot) / "sessions").glob("S-*")):
        meta = load_meta(directory)
        if meta.status in ("withdrawn", "discarded") or not (directory / "grading" / "grade.json").is_file():
            continue
        sessions[meta.session] = load_session_data(directory, packages[meta.package_id])

    reviews, overall = _load_reviews(Path(pilot), packages, sessions)
    reviews = [r for r in reviews if r.session in sessions]

    # Only runs that are recorded, current and decided count: a pass or a fail from V7.
    agent_alone: dict[str, tuple[int, int]] = {}
    for package in packages.values():
        acknowledgements, _ = load_acknowledgements(package.root)
        evaluation = evaluate_agent_alone(package, acknowledgements)
        if evaluation.table:
            agent_alone[package.meta.id] = (evaluation.table["full_solves"], evaluation.table["runs"])

    gate = compute_gate(list(sessions.values()), reviews, overall, agent_alone=agent_alone)
    return render_gate(gate, _hours(package_dirs), _fairness(Path(pilot)))
