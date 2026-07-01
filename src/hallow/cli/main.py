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
    from hallow.output import format_results

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

    cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
    with cm as fh:
        output = format_results(results, fmt=fmt, file=fh)
        if fmt == "json" and not fh:
            typer.echo(output)

    if ci and results.errors > 0:
        raise typer.Exit(code=1)


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


if __name__ == "__main__":
    app()
