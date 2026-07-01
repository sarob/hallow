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

    for cycle in results.cycles:
        chain = " -> ".join(cycle.modules + [cycle.modules[0]])
        lines.append(f"::error file={cycle.modules[0]},line=1::circular-dependencies: {chain}")

    for group in results.duplicates:
        first = group.fragments[0]
        locs = ", ".join(f"{f.file}:{f.start_line}" for f in group.fragments)
        lines.append(
            f"::warning file={first.file},line={first.start_line}"
            f"::duplicate-code: {group.token_count} tokens duplicated at {locs}"
        )

    text = "\n".join(lines) + "\n" if lines else ""
    if file:
        file.write(text)
    return text
