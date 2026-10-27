"""Tests for result totals."""

from __future__ import annotations

from hallow.types import (
    AnalysisResults,
    DuplicateFragment,
    DuplicateGroup,
    Finding,
    ImportCycle,
    Location,
    RuleId,
    Severity,
)


def test_cycles_and_duplicates_are_not_counted_twice():
    # Regression: each cycle and clone group already has a finding, and
    # compute_totals added them again (observed on a large project: 1243 total vs 699 findings).
    results = AnalysisResults(
        findings=[
            Finding(rule=RuleId.CIRCULAR_DEPENDENCIES, message="c", location=Location(file="a.py")),
            Finding(
                rule=RuleId.DUPLICATE_CODE,
                severity=Severity.WARN,
                message="d",
                location=Location(file="a.py"),
            ),
        ],
        cycles=[ImportCycle(modules=["a.py", "b.py"])],
        duplicates=[
            DuplicateGroup(fragments=[DuplicateFragment(file="a.py", start_line=1, end_line=9)])
        ],
    )
    results.compute_totals()
    assert results.total_issues == 2 == results.errors + results.warnings


def _cycle_and_clone_project(root):
    block = "\n".join(f"    v{i} = compute({i}) + offset_{i}" for i in range(20))
    (root / "a.py").write_text(f"import b\ndef fa():\n{block}\n")
    (root / "b.py").write_text(f"import a\ndef fb():\n{block}\n")


def test_totals_through_analysis_count_each_reported_finding_once(tmp_path):
    from hallow.config.loader import HallowConfig
    from hallow.core import analyze

    _cycle_and_clone_project(tmp_path)
    cfg = HallowConfig(root=tmp_path)
    cfg.duplicates.min_tokens = 30
    results = analyze(cfg)
    rules = [f.rule.value for f in results.findings]
    assert results.cycles and results.duplicates
    assert rules.count("circular-dependencies") == len(results.cycles)
    assert rules.count("duplicate-code") == len(results.duplicates)
    assert results.total_issues == len(results.findings) == results.errors + results.warnings


def test_disabled_rules_leave_data_but_no_issues(tmp_path):
    from hallow.config.loader import HallowConfig
    from hallow.core import analyze

    _cycle_and_clone_project(tmp_path)
    cfg = HallowConfig(root=tmp_path)
    cfg.duplicates.min_tokens = 30
    cfg.rules.overrides["circular-dependencies"] = Severity.OFF
    cfg.rules.overrides["duplicate-code"] = Severity.OFF
    results = analyze(cfg)
    assert results.cycles and results.duplicates  # supporting data kept
    rules = {f.rule.value for f in results.findings}
    assert not {"circular-dependencies", "duplicate-code"} & rules
    assert results.total_issues == len(results.findings)
