"""Tests for output formatters — SARIF, Markdown, CodeClimate, GitHub."""

from __future__ import annotations

import json

from hallow.output.codeclimate import format_codeclimate
from hallow.output.github import format_github
from hallow.output.markdown import format_markdown
from hallow.output.sarif import format_sarif
from hallow.types import (
    AnalysisResults,
    Finding,
    ImportCycle,
    Location,
    ProjectHealth,
    RuleId,
    Severity,
)


def _sample_results() -> AnalysisResults:
    results = AnalysisResults(
        findings=[
            Finding(
                rule=RuleId.UNUSED_IMPORTS,
                severity=Severity.ERROR,
                message="'os' imported but unused",
                location=Location(file="app.py", line=1),
            ),
            Finding(
                rule=RuleId.HIGH_COMPLEXITY,
                severity=Severity.WARN,
                message="'process' has cyclomatic complexity 25 (max 20)",
                location=Location(file="core.py", line=10),
            ),
        ],
        cycles=[
            ImportCycle(modules=["a.py", "b.py"], edges=[("a.py", "b.py"), ("b.py", "a.py")]),
        ],
        health=ProjectHealth(score=75, grade="B", total_files=5),
        total_files_scanned=5,
    )
    results.compute_totals()
    return results


def _empty_results() -> AnalysisResults:
    return AnalysisResults(total_files_scanned=3)


def test_sarif_structure():
    results = _sample_results()
    text = format_sarif(results)
    sarif = json.loads(text)
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "hallow"
    assert len(run["results"]) == 3  # 2 findings + 1 cycle


def test_sarif_empty():
    results = _empty_results()
    text = format_sarif(results)
    sarif = json.loads(text)
    assert len(sarif["runs"][0]["results"]) == 0


def test_sarif_rules_deduped():
    results = AnalysisResults(
        findings=[
            Finding(
                rule=RuleId.UNUSED_IMPORTS,
                severity=Severity.ERROR,
                message="'os' unused",
                location=Location(file="a.py", line=1),
            ),
            Finding(
                rule=RuleId.UNUSED_IMPORTS,
                severity=Severity.ERROR,
                message="'sys' unused",
                location=Location(file="b.py", line=1),
            ),
        ],
        total_files_scanned=2,
    )
    text = format_sarif(results)
    sarif = json.loads(text)
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) == 1


def test_markdown_has_table():
    results = _sample_results()
    text = format_markdown(results)
    assert "# Hallow Report" in text
    assert "| Severity |" in text
    assert "'os' imported but unused" in text
    assert "Circular Dependencies" in text


def test_markdown_empty():
    results = _empty_results()
    text = format_markdown(results)
    assert "No issues found" in text


def test_codeclimate_structure():
    results = _sample_results()
    text = format_codeclimate(results)
    issues = json.loads(text)
    assert isinstance(issues, list)
    assert len(issues) == 3
    assert all("fingerprint" in i for i in issues)
    assert all("location" in i for i in issues)


def test_codeclimate_categories():
    results = _sample_results()
    text = format_codeclimate(results)
    issues = json.loads(text)
    categories = {i["check_name"]: i["categories"][0] for i in issues}
    assert categories["unused-imports"] == "Clarity"
    assert categories["high-complexity"] == "Complexity"
    assert categories["circular-dependencies"] == "Complexity"


def test_github_annotations():
    results = _sample_results()
    text = format_github(results)
    lines = text.strip().split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("::error file=app.py")
    assert "unused-imports" in lines[0]
    assert lines[1].startswith("::warning file=core.py")
    assert lines[2].startswith("::error file=a.py")


def test_github_empty():
    results = _empty_results()
    text = format_github(results)
    assert text == ""


def test_format_results_dispatches():
    from hallow.output import format_results

    results = _sample_results()
    for fmt in ("json", "sarif", "markdown", "codeclimate", "github", "compact"):
        output = format_results(results, fmt=fmt)
        assert output is not None or fmt in ("human", "compact")
