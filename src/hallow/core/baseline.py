"""Baselines — record known findings by canonical key, report only new ones.

A baseline file (after fallow's baselines) records what saved it and one
canonical key per finding occurrence:

    {"kind": "check", "identity": "hl1", "saved_by": "hallow 0.1.0",
     "entries": ["unused-imports:app.py:os", ...]}

N saved occurrences of a key hide at most N current findings with that key.
Keys carry no line numbers, so moving code does not invalidate a baseline.
A file of another kind (or the line-keyed format hallow wrote before) is
reported as unrecognised and suppresses nothing.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from hallow import __version__
from hallow.identity import IDENTITY
from hallow.types import AnalysisResults, BaselineStaleness, Finding

BASELINE_KIND = "check"


@dataclass
class Baseline:
    entries: Counter[str] = field(default_factory=Counter)
    recognised: bool = True
    saved_by: str | None = None


def baseline_document(findings: list[Finding]) -> dict:
    return {
        "kind": BASELINE_KIND,
        "identity": IDENTITY,
        "saved_by": f"hallow {__version__}",
        "entries": sorted(f.key for f in findings),
    }


def save_baseline(results: AnalysisResults, path: Path) -> int:
    data = baseline_document(results.findings)
    _write(path, data)
    return len(data["entries"])


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def parse_baseline(text: str) -> Baseline:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return Baseline(recognised=False)
    if (
        not isinstance(data, dict)
        or data.get("kind") != BASELINE_KIND
        or data.get("identity") != IDENTITY
        or not isinstance(data.get("entries"), list)
    ):
        saved_by = data.get("saved_by") if isinstance(data, dict) else None
        return Baseline(recognised=False, saved_by=saved_by)
    return Baseline(entries=Counter(str(e) for e in data["entries"]), saved_by=data.get("saved_by"))


def load_baseline(path: Path) -> Baseline:
    """Read a baseline. A missing file is an empty, recognised baseline."""
    if not path.exists():
        return Baseline()
    try:
        return parse_baseline(path.read_text(encoding="utf-8"))
    except OSError:
        return Baseline(recognised=False)


def match_baseline(findings: list[Finding], baseline: Baseline) -> tuple[list[Finding], Counter]:
    """Split off the findings the baseline covers; return (uncovered, matched entries)."""
    remaining = Counter(baseline.entries)
    matched: Counter[str] = Counter()
    new: list[Finding] = []
    for f in findings:
        if remaining[f.key] > 0:
            remaining[f.key] -= 1
            matched[f.key] += 1
        else:
            new.append(f)
    return new, matched


def filter_baseline(
    results: AnalysisResults,
    baseline_path: Path,
    change_scoped: bool = False,
) -> AnalysisResults:
    """Drop baselined findings and attach a staleness report.

    A `change_scoped` run (limited to changed files) sees only part of the
    project, so unmatched entries there are expected and raise no warning.
    """
    baseline = load_baseline(baseline_path)
    new, matched = match_baseline(results.findings, baseline)

    total = sum(baseline.entries.values())
    matched_n = sum(matched.values())
    stale_n = total - matched_n
    warning = "none"
    # A full scan saw every finding the baseline could match, so unmatched
    # entries are stale even when the run is clean (every finding fixed).
    if not change_scoped and total:
        if matched_n == 0:
            warning = "zero-overlap"
        elif stale_n * 4 >= total:
            warning = "partial"

    filtered = results.model_copy()
    filtered.findings = new
    filtered.baseline_staleness = BaselineStaleness(
        path=str(baseline_path),
        baseline_entries=total,
        matched_entries=matched_n,
        stale_entries=stale_n,
        current_findings=len(results.findings),
        stale=stale_n > 0,
        warning=warning,
        unrecognised_format=not baseline.recognised,
        saved_by=baseline.saved_by,
    )
    filtered.compute_totals()
    return filtered


def prune_baseline(results: AnalysisResults, path: Path, check: bool) -> dict:
    """Remove entries no current finding matches; never add any.

    Returns one `files[]` item of the `baselines-prune` output.
    """
    item: dict = {"kind": BASELINE_KIND, "path": str(path)}
    if not path.exists():
        return {**item, "status": "error", "reason": "baseline file not found"}

    baseline = load_baseline(path)
    if not baseline.recognised:
        return {**item, "status": "refused", "reason": "not a hallow check baseline"}

    _, matched = match_baseline(results.findings, baseline)
    removed = baseline.entries - matched
    item.update(
        entries_before=sum(baseline.entries.values()),
        entries_after=sum(matched.values()),
        removed=[{"key": k, "count": n} for k, n in sorted(removed.items())],
    )
    if not removed:
        return {**item, "status": "unchanged"}
    if check:
        return {**item, "status": "would-prune"}

    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"] = sorted(matched.elements())
    _write(path, data)
    return {**item, "status": "pruned"}
