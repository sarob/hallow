"""Baseline support — suppress known issues, only report new ones."""

from __future__ import annotations

import json
from pathlib import Path

from hallow.types import AnalysisResults, Finding


def save_baseline(results: AnalysisResults, path: Path) -> int:
    entries = [_finding_key(f) for f in results.findings]
    data = {"version": 1, "findings": sorted(set(entries))}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return len(data["findings"])


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("findings", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def filter_baseline(results: AnalysisResults, baseline_path: Path) -> AnalysisResults:
    known = load_baseline(baseline_path)
    if not known:
        return results

    new_findings = [f for f in results.findings if _finding_key(f) not in known]

    filtered = results.model_copy()
    filtered.findings = new_findings
    filtered.compute_totals()
    return filtered


def _finding_key(finding: Finding) -> str:
    return f"{finding.rule.value}:{finding.location.file}:{finding.location.line}:{finding.message}"
