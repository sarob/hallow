"""Output formatters for AnalysisResults."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from hallow.types import (
    AnalysisResults,
    DuplicateGroup,
    FileHealth,
    Finding,
    ProjectHealth,
    Severity,
)


def format_results(results: AnalysisResults, fmt: str = "human", file: Any = None) -> str | None:
    formatter = get_formatter(fmt)
    return formatter(results, file=file)


def get_formatter(fmt: str):  # noqa: ANN201
    from hallow.output.codeclimate import format_codeclimate
    from hallow.output.github import format_github
    from hallow.output.markdown import format_markdown
    from hallow.output.sarif import format_sarif

    formatters = {
        "human": format_human,
        "json": format_json,
        "compact": format_compact,
        "sarif": format_sarif,
        "markdown": format_markdown,
        "codeclimate": format_codeclimate,
        "github": format_github,
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
        if results.health:
            _print_health_summary(console, results.health)
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
            chain = " -> ".join(cycle.modules + [cycle.modules[0]])
            console.print(f"  [red]<>[/red] {chain}")
        console.print()

    if results.duplicates:
        console.print(f"[bold]Duplicate code:[/bold] {len(results.duplicates)} group(s)")
        for group in results.duplicates[:5]:
            locs = ", ".join(f"{f.file}:{f.start_line}" for f in group.fragments)
            console.print(
                f"  [yellow]D[/yellow] {group.token_count} tokens, "
                f"{group.line_count} lines — {locs}"
            )
        if len(results.duplicates) > 5:
            console.print(f"  ... and {len(results.duplicates) - 5} more")
        console.print()

    if results.health:
        _print_health_summary(console, results.health)

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


def _print_health_summary(console: Console, health: ProjectHealth) -> None:
    grade_color = {
        "A": "green",
        "B": "blue",
        "C": "yellow",
        "D": "red",
        "F": "bold red",
    }.get(health.grade, "white")

    console.print(
        f"  Health: [{grade_color}]{health.score}/100 ({health.grade})[/{grade_color}]"
        f"  Complexity hotspots: {len(health.hotspots)}"
        f"  Maintainability: {health.maintainability_avg} avg"
    )


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
        chain = " -> ".join(cycle.modules)
        console.print(f"circular-dependencies: {chain}")


def format_health(
    project: ProjectHealth,
    file_healths: list[FileHealth],
    fmt: str = "human",
    score_only: bool = False,
    file: Any = None,
) -> None:
    if fmt == "json":
        output = {
            "project": project.model_dump(mode="json"),
            "files": [fh.model_dump(mode="json") for fh in file_healths],
        }
        text = json.dumps(output, indent=2)
        if file:
            file.write(text)
        else:
            print(text)  # noqa: T201
        return

    console = Console(file=file, highlight=False)

    grade_color = {
        "A": "green",
        "B": "blue",
        "C": "yellow",
        "D": "red",
        "F": "bold red",
    }.get(project.grade, "white")

    console.print()
    console.print(f"  Health: [{grade_color}]{project.score}/100 ({project.grade})[/{grade_color}]")
    console.print(f"  Complexity hotspots: {len(project.hotspots)}")
    console.print(f"  Maintainability: {project.maintainability_avg} avg")
    console.print(
        f"  Files: {project.total_files}  "
        f"Functions: {project.total_functions}  "
        f"Lines: {project.total_lines}"
    )
    console.print()

    if score_only:
        return

    if project.hotspots:
        console.print("[bold]Complexity hotspots:[/bold]")
        for fh in project.hotspots:
            console.print(
                f"  [yellow]![/yellow] {fh.path}"
                f"  cc_max={fh.cyclomatic_max}"
                f"  cog_max={fh.cognitive_max}"
                f"  MI={fh.maintainability_index}"
            )
            for fname in fh.hotspot_functions:
                console.print(f"    -> {fname}")
        console.print()

    if file_healths:
        table = Table(title="File Health", show_lines=False)
        table.add_column("File", style="bold")
        table.add_column("LOC", justify="right")
        table.add_column("CC avg", justify="right")
        table.add_column("CC max", justify="right")
        table.add_column("Cog avg", justify="right")
        table.add_column("MI", justify="right")

        for fh in sorted(file_healths, key=lambda h: h.maintainability_index):
            mi_style = (
                "green"
                if fh.maintainability_index >= 65
                else ("yellow" if fh.maintainability_index >= 40 else "red")
            )
            table.add_row(
                fh.path,
                str(fh.lines_of_code),
                f"{fh.cyclomatic_avg:.1f}",
                str(fh.cyclomatic_max),
                f"{fh.cognitive_avg:.1f}",
                f"[{mi_style}]{fh.maintainability_index:.1f}[/{mi_style}]",
            )

        console.print(table)
        console.print()


def format_dupes(
    groups: list[DuplicateGroup],
    fmt: str = "human",
    file: Any = None,
) -> None:
    if fmt == "json":
        output = [g.model_dump(mode="json") for g in groups]
        text = json.dumps(output, indent=2)
        if file:
            file.write(text)
        else:
            print(text)  # noqa: T201
        return

    console = Console(file=file, highlight=False)

    if not groups:
        console.print()
        console.print("[bold green]No duplicate code found.[/bold green]")
        console.print()
        return

    console.print()
    console.print(f"[bold]Found {len(groups)} duplicate group(s):[/bold]")
    console.print()

    for i, group in enumerate(groups, 1):
        console.print(
            f"  [bold]Group {i}[/bold] — "
            f"{group.token_count} tokens, {group.line_count} lines, "
            f"{len(group.fragments)} occurrences"
        )
        for frag in group.fragments:
            console.print(
                f"    {frag.file}:{frag.start_line}-{frag.end_line} ({frag.lines_of_code} lines)"
            )
        console.print()
