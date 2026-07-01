"""Markdown output — for PR comments and reports."""

from __future__ import annotations

from typing import Any

from hallow.types import AnalysisResults, Severity


def format_markdown(results: AnalysisResults, file: Any = None) -> str:
    lines: list[str] = []

    lines.append("# Hallow Report")
    lines.append("")

    if results.health:
        h = results.health
        lines.append(f"**Health:** {h.score}/100 ({h.grade})")
        lines.append(
            f"**Files:** {h.total_files} | "
            f"**Functions:** {h.total_functions} | "
            f"**Lines:** {h.total_lines}"
        )
        lines.append("")

    if not results.findings and not results.cycles and not results.duplicates:
        lines.append("No issues found.")
        lines.append("")
        lines.append(f"Scanned {results.total_files_scanned} files.")
        return _output(lines, file)

    if results.findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("| Severity | Rule | File | Line | Message |")
        lines.append("|----------|------|------|------|---------|")

        for finding in results.findings:
            sev = _severity_emoji(finding.severity)
            lines.append(
                f"| {sev} | `{finding.rule.value}` "
                f"| `{finding.location.file}` "
                f"| {finding.location.line} "
                f"| {finding.message} |"
            )
        lines.append("")

    if results.cycles:
        lines.append("## Circular Dependencies")
        lines.append("")
        for cycle in results.cycles:
            chain = " -> ".join(cycle.modules + [cycle.modules[0]])
            lines.append(f"- {chain}")
        lines.append("")

    if results.duplicates:
        lines.append("## Duplicate Code")
        lines.append("")
        for i, group in enumerate(results.duplicates, 1):
            locs = ", ".join(f"`{f.file}:{f.start_line}-{f.end_line}`" for f in group.fragments)
            lines.append(
                f"{i}. **{group.token_count} tokens, {group.line_count} lines** "
                f"({len(group.fragments)} occurrences): {locs}"
            )
        lines.append("")

    lines.append("---")
    summary = []
    if results.errors:
        summary.append(f"{results.errors} error(s)")
    if results.warnings:
        summary.append(f"{results.warnings} warning(s)")
    if not summary:
        summary.append("0 issues")
    lines.append(f"{' and '.join(summary)} in {results.total_files_scanned} files.")

    return _output(lines, file)


def _output(lines: list[str], file: Any) -> str:
    text = "\n".join(lines) + "\n"
    if file:
        file.write(text)
    return text


def _severity_emoji(severity: Severity) -> str:
    return {
        Severity.ERROR: "ERROR",
        Severity.WARN: "WARN",
        Severity.OFF: "OFF",
    }[severity]
