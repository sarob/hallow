"""Hallow CLI — deterministic codebase intelligence for Python."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer

from hallow import __version__

app = typer.Typer(
    name="hallow",
    help="Deterministic codebase intelligence for Python.",
    no_args_is_help=False,
    add_completion=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hallow {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    format: str = typer.Option(
        "human",
        "--format",
        "-f",
        help="Output format: human, json, compact",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="CI mode — exit 1 on any error-level issue",
    ),
    changed_since: str | None = typer.Option(
        None,
        "--changed-since",
        help="Only check files changed since this git ref",
    ),
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    _run_check(
        root=root,
        config_path=config,
        fmt=format,
        output_file=output_file,
        ci=ci,
        changed_since=changed_since,
    )


@app.command()
def check(
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    format: str = typer.Option(
        "human",
        "--format",
        "-f",
        help="Output format: human, json, compact",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="CI mode — exit 1 on any error-level issue",
    ),
    changed_since: str | None = typer.Option(
        None,
        "--changed-since",
        help="Only check files changed since this git ref",
    ),
) -> None:
    """Run dead code analysis."""
    _run_check(
        root=root,
        config_path=config,
        fmt=format,
        output_file=output_file,
        ci=ci,
        changed_since=changed_since,
    )


@app.command()
def health(
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    format: str = typer.Option(
        "human",
        "--format",
        "-f",
        help="Output format: human, json",
    ),
    score: bool = typer.Option(
        False,
        "--score",
        help="Show project-level health score summary",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file",
    ),
) -> None:
    """Compute project health score and complexity metrics."""
    _run_health(
        root=root,
        config_path=config,
        fmt=format,
        score_only=score,
        output_file=output_file,
    )


@app.command()
def dupes(
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    mode: str = typer.Option(
        "mild",
        "--mode",
        "-m",
        help="Detection mode: strict, mild, weak, semantic",
    ),
    min_tokens: int = typer.Option(
        50,
        "--min-tokens",
        help="Minimum token count for a duplicate group",
    ),
    min_lines: int = typer.Option(
        5,
        "--min-lines",
        help="Minimum line count for a duplicate fragment",
    ),
    format: str = typer.Option(
        "human",
        "--format",
        "-f",
        help="Output format: human, json",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file",
    ),
) -> None:
    """Detect duplicate code blocks."""
    _run_dupes(
        root=root,
        config_path=config,
        mode=mode,
        min_tokens=min_tokens,
        min_lines=min_lines,
        fmt=format,
        output_file=output_file,
    )


@app.command()
def fix(
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview changes without applying (default: dry run)",
    ),
) -> None:
    """Auto-fix unused imports, dead files, and unused dependencies."""
    _run_fix(root=root, config_path=config, dry_run=dry_run)


@app.command()
def audit(
    root: Path | None = typer.Option(
        None,
        "--root",
        "-r",
        help="Project root directory",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file path",
    ),
    changed_since: str = typer.Option(
        "origin/main",
        "--changed-since",
        help="Git ref to diff against (default: origin/main)",
    ),
    format: str = typer.Option(
        "human",
        "--format",
        "-f",
        help="Output format: human, json, sarif, markdown, codeclimate, github, compact",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to file",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="CI mode — exit 1 on any error-level issue",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Baseline file — only report findings not in baseline",
    ),
    save_baseline: Path | None = typer.Option(
        None,
        "--save-baseline",
        help="Save current findings as a new baseline file",
    ),
) -> None:
    """PR-scoped audit — only flag issues in changed files."""
    _run_audit(
        root=root,
        config_path=config,
        changed_since=changed_since,
        fmt=format,
        output_file=output_file,
        ci=ci,
        baseline_path=baseline,
        save_baseline_path=save_baseline,
    )


def _run_check(
    root: Path | None,
    config_path: Path | None,
    fmt: str,
    output_file: Path | None,
    ci: bool,
    changed_since: str | None,
) -> None:
    from hallow.config import load_config
    from hallow.core import analyze

    overrides: dict = {}
    if ci:
        overrides["ci"] = True
        overrides["fail_on_issues"] = True
    if changed_since:
        overrides["changed_since"] = changed_since

    cfg = load_config(
        root=root,
        config_path=config_path,
        overrides=overrides or None,
    )
    results = analyze(cfg)

    _emit_results(results, fmt, output_file)

    if ci and results.errors > 0:
        raise typer.Exit(code=1)


def _run_audit(
    root: Path | None,
    config_path: Path | None,
    changed_since: str,
    fmt: str,
    output_file: Path | None,
    ci: bool,
    baseline_path: Path | None,
    save_baseline_path: Path | None,
) -> None:
    from hallow.config import load_config
    from hallow.core import analyze
    from hallow.core.baseline import filter_baseline, save_baseline

    overrides: dict = {"changed_since": changed_since}
    if ci:
        overrides["ci"] = True
        overrides["fail_on_issues"] = True

    cfg = load_config(root=root, config_path=config_path, overrides=overrides)
    results = analyze(cfg)

    if save_baseline_path:
        count = save_baseline(results, save_baseline_path)
        typer.echo(f"Saved baseline with {count} finding(s) to {save_baseline_path}")

    if baseline_path:
        results = filter_baseline(results, baseline_path)

    _emit_results(results, fmt, output_file)

    if ci and results.errors > 0:
        raise typer.Exit(code=1)


def _emit_results(results, fmt: str, output_file: Path | None) -> None:
    from hallow.output import format_results

    text_formats = {"json", "sarif", "markdown", "codeclimate", "github"}
    cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
    with cm as fh:
        output = format_results(results, fmt=fmt, file=fh)
        if fmt in text_formats and not fh and output:
            typer.echo(output)


def _run_health(
    root: Path | None,
    config_path: Path | None,
    fmt: str,
    score_only: bool,
    output_file: Path | None,
) -> None:
    from hallow.config import load_config
    from hallow.core.discovery import discover_python_files
    from hallow.core.health import compute_file_health, compute_project_health
    from hallow.extract import extract_modules_parallel
    from hallow.output.formatters import format_health

    cfg = load_config(root=root, config_path=config_path)
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)
    modules = extract_modules_parallel(files, root_path)

    project = compute_project_health(modules, cfg)
    file_healths = [
        compute_file_health(p, m, cfg)
        for p, m in sorted(modules.items())
        if not m.is_test and not m.is_conftest
    ]

    cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
    with cm as fh:
        format_health(project, file_healths, fmt=fmt, score_only=score_only, file=fh)


def _run_dupes(
    root: Path | None,
    config_path: Path | None,
    mode: str,
    min_tokens: int,
    min_lines: int,
    fmt: str,
    output_file: Path | None,
) -> None:
    from hallow.config import load_config
    from hallow.core.discovery import discover_python_files
    from hallow.core.duplicates import detect_duplicates
    from hallow.output.formatters import format_dupes

    overrides: dict = {
        "duplicates": {
            "mode": mode,
            "min_tokens": min_tokens,
            "min_lines": min_lines,
        }
    }
    cfg = load_config(root=root, config_path=config_path, overrides=overrides)
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)

    groups, _ = detect_duplicates(files, root_path, cfg)

    cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
    with cm as fh:
        format_dupes(groups, fmt=fmt, file=fh)


def _run_fix(
    root: Path | None,
    config_path: Path | None,
    dry_run: bool,
) -> None:
    from rich.console import Console

    from hallow.config import load_config
    from hallow.core import analyze
    from hallow.core.fixer import apply_fixes

    console = Console(highlight=False)

    cfg = load_config(root=root, config_path=config_path)
    results = analyze(cfg)
    fix_result = apply_fixes(results, cfg.root.resolve(), dry_run=dry_run)

    if fix_result.total == 0:
        console.print()
        console.print("[bold green]Nothing to fix.[/bold green]")
        console.print()
        return

    prefix = "Would" if dry_run else "Did"
    console.print()

    if fix_result.removed_imports:
        console.print(f"  {prefix} remove: {len(fix_result.removed_imports)} unused import(s)")
        for desc in fix_result.removed_imports[:10]:
            console.print(f"    - {desc}")
        if len(fix_result.removed_imports) > 10:
            console.print(f"    ... and {len(fix_result.removed_imports) - 10} more")

    if fix_result.deleted_files:
        console.print(f"  {prefix} delete: {len(fix_result.deleted_files)} dead file(s)")
        for f in fix_result.deleted_files:
            console.print(f"    - {f}")

    if fix_result.removed_deps:
        console.print(f"  {prefix} remove: {len(fix_result.removed_deps)} unused dep(s)")
        for d in fix_result.removed_deps:
            console.print(f"    - {d}")

    console.print()
    if dry_run:
        console.print("  [dim]Run with --apply to make changes.[/dim]")
        console.print()


if __name__ == "__main__":
    app()
