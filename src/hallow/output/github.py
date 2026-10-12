"""GitHub Actions annotations output — ::error and ::warning workflow commands."""

from __future__ import annotations

from typing import Any

from hallow.types import AnalysisResults, Severity


def format_github(results: AnalysisResults, file: Any = None) -> str:
    lines: list[str] = []

    for finding in results.findings:
        cmd = "error" if finding.severity == Severity.ERROR else "warning"
        loc = finding.location
        annotation = (
            f"::{cmd} file={loc.file},line={max(loc.line, 1)}"
            f"::{finding.rule.value}: {finding.message}"
        )
        lines.append(annotation)

    text = "\n".join(lines) + "\n" if lines else ""
    if file:
        file.write(text)
    return text
