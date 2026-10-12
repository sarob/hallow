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
    # compute_totals added them again (fd-platform: 1243 total vs 699 findings).
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
