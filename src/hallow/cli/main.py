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


if __name__ == "__main__":
    app()
