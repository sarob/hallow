"""Output formatters for AnalysisResults."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from hallow.types import AnalysisResults, Finding, Severity


def format_results(results: AnalysisResults, fmt: str = "human", file: Any = None) -> str | None:
    formatter = get_formatter(fmt)
    return formatter(results, file=file)


def get_formatter(fmt: str):  # noqa: ANN201
    formatters = {
        "human": format_human,
        "json": format_json,
        "compact": format_compact,
    }
    if fmt not in formatters:
        raise ValueError(f"Unknown format: {fmt}. Available: {', '.join(formatters)}")
    return formatters[fmt]


def _severity_style(severity: Severity) -> str:
    return {
        Severity.ERROR: "bold red",
        Severity.WARN: "yellow",
        Severity.OFF: "dim",
    }[severity]


def _severity_icon(severity: Severity) -> str:
    return {
        Severity.ERROR: "E",
        Severity.WARN: "W",
        Severity.OFF: "-",
    }[severity]


def format_human(results: AnalysisResults, file: Any = None) -> None:
    console = Console(file=file, highlight=False)

    if not results.findings and not results.cycles:
        console.print()
        console.print("[bold green]No issues found.[/bold green]", highlight=False)
        console.print(f"  Scanned {results.total_files_scanned} files", style="dim")
        console.print()
        return

    grouped: dict[str, list[Finding]] = {}
    for finding in results.findings:
        key = finding.location.file
        grouped.setdefault(key, []).append(finding)

    console.print()

    for filepath, findings in sorted(grouped.items()):
        console.print(f"[bold]{filepath}[/bold]")
        for f in findings:
            style = _severity_style(f.severity)
            icon = _severity_icon(f.severity)
            line_ref = f":{f.location.line}" if f.location.line else ""
            console.print(
                f"  [{style}]{icon}[/{style}] "
                f"[dim]{f.rule.value}[/dim] "
                f"{f.message} "
                f"[dim]{filepath}{line_ref}[/dim]"
            )
        console.print()

    if results.cycles:
        console.print("[bold]Circular imports:[/bold]")
        for cycle in results.cycles:
            chain = " → ".join(cycle.modules + [cycle.modules[0]])
            console.print(f"  [red]↻[/red] {chain}")
        console.print()

    summary_parts = []
    if results.errors:
        n = results.errors
        s = "s" if n != 1 else ""
        summary_parts.append(f"[bold red]{n} error{s}[/bold red]")
    if results.warnings:
        n = results.warnings
        s = "s" if n != 1 else ""
        summary_parts.append(f"[yellow]{n} warning{s}[/yellow]")
    if not summary_parts:
        summary_parts.append("[green]0 issues[/green]")

    console.print(
        f"  {' and '.join(summary_parts)} in {results.total_files_scanned} files",
        highlight=False,
    )
    console.print()


def format_json(results: AnalysisResults, file: Any = None) -> str:
    output = results.model_dump(mode="json")
    text = json.dumps(output, indent=2)
    if file:
        file.write(text)
        return text
    return text


def format_compact(results: AnalysisResults, file: Any = None) -> None:
    console = Console(file=file, highlight=False)

    for finding in results.findings:
        icon = _severity_icon(finding.severity)
        line = finding.location.line
        console.print(
            f"{finding.location.file}:{line}: {icon} {finding.rule.value}: {finding.message}"
        )

    for cycle in results.cycles:
        chain = " → ".join(cycle.modules)
        console.print(f"circular-dependencies: {chain}")
