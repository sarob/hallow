"""Tests for baseline support."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.core.baseline import filter_baseline, load_baseline, save_baseline
from hallow.types import AnalysisResults, Finding, Location, RuleId, Severity


def _make_finding(file: str, line: int, message: str) -> Finding:
    return Finding(
        rule=RuleId.UNUSED_IMPORTS,
        severity=Severity.ERROR,
        message=message,
        location=Location(file=file, line=line),
    )


def test_save_and_load_baseline():
    with TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "baseline.json"
        results = AnalysisResults(
            findings=[
                _make_finding("a.py", 1, "'os' unused"),
                _make_finding("b.py", 5, "'sys' unused"),
            ]
        )
        count = save_baseline(results, baseline_path)
        assert count == 2
        assert baseline_path.exists()

        known = load_baseline(baseline_path)
        assert len(known) == 2


def test_filter_removes_known():
    with TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "baseline.json"
        known_finding = _make_finding("a.py", 1, "'os' unused")
        new_finding = _make_finding("c.py", 3, "'json' unused")

        baseline_results = AnalysisResults(findings=[known_finding])
        save_baseline(baseline_results, baseline_path)

        full_results = AnalysisResults(findings=[known_finding, new_finding])
        full_results.compute_totals()

        filtered = filter_baseline(full_results, baseline_path)
        assert len(filtered.findings) == 1
        assert filtered.findings[0].message == "'json' unused"


def test_filter_no_baseline_returns_all():
    with TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "nonexistent.json"
        results = AnalysisResults(findings=[_make_finding("a.py", 1, "'os' unused")])
        results.compute_totals()

        filtered = filter_baseline(results, baseline_path)
        assert len(filtered.findings) == 1


def test_load_empty_baseline():
    with TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "baseline.json"
        baseline_path.write_text("{}")
        known = load_baseline(baseline_path)
        assert len(known) == 0


def test_load_corrupt_baseline():
    with TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "baseline.json"
        baseline_path.write_text("not json")
        known = load_baseline(baseline_path)
        assert len(known) == 0
