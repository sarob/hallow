"""Hallow CLI — deterministic codebase intelligence for Python."""

from __future__ import annotations

import contextlib
import json
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from hallow import __version__
from hallow.cli.options import CHECK_FORMATS, DOC_FORMATS, DUPE_MODES
from hallow.exit_codes import INVALID_INPUT, ISSUES, UNAVAILABLE, HallowError

app = typer.Typer(
    name="hallow",
    help="Deterministic codebase intelligence for Python.",
    no_args_is_help=False,
    add_completion=True,
)

# ── Shared options ──

RootOpt = Annotated[Path | None, typer.Option("--root", "-r", help="Project root directory")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Config file path")]
OutputOpt = Annotated[Path | None, typer.Option("--output", "-o", help="Write output to file")]
QuietOpt = Annotated[
    bool,
    typer.Option("--quiet", "-q", help="Suppress status messages on stderr; output is unchanged"),
]
CiOpt = Annotated[
    bool,
    typer.Option(
        "--ci",
        help="Equivalent to --format sarif --fail-on-issues --quiet (an explicit --format wins)",
    ),
]
FailOnIssuesOpt = Annotated[
    bool,
    typer.Option(
        "--fail-on-issues",
        help="Exit 1 on warn-severity findings too, not only on error-severity ones",
    ),
]
ChangedSinceOpt = Annotated[
    str | None,
    typer.Option("--changed-since", help="Only check files changed since this git ref"),
]
BaselineOpt = Annotated[
    Path | None,
    typer.Option("--baseline", help="Baseline file — only report findings not in baseline"),
]
SaveBaselineOpt = Annotated[
    Path | None,
    typer.Option("--save-baseline", help="Save current findings as a new baseline file"),
]
FailOnStaleOpt = Annotated[
    bool,
    typer.Option(
        "--fail-on-stale-baseline",
        help="Exit 1 if any --baseline entry matches no finding (or the file is not a baseline)",
    ),
]


def _format_opt(formats: tuple[str, ...]):  # noqa: ANN202
    return typer.Option(
        None,
        "--format",
        "-f",
        help=f"Output format: {', '.join(formats)} (default: human)",
    )


def _validate_preset(value: str | None) -> str | None:
    from hallow.core.boundaries import get_preset

    if value is not None and get_preset(value) is None:
        raise typer.BadParameter("expected one of: layered, hexagonal, feature-sliced")
    return value


BoundariesOpt = Annotated[
    str | None,
    typer.Option(
        "--boundaries",
        help="Check architecture boundaries with a preset: layered, hexagonal, feature-sliced",
        callback=_validate_preset,
    ),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hallow {__version__}")
        raise typer.Exit()


# ── Error handling and output ──


def _guarded(fmt: str | None, body: Callable[[], int]) -> None:
    """Run a command body; map its result and errors to exit codes.

    With `--format json`, an error raised while the command runs is a JSON
    document on stdout (`{"error": true, "message", "exit_code"}`) rather
    than a traceback. Command-line usage errors (unknown option, invalid
    `--boundaries` value, options before the command) are reported by the
    argument parser before this runs: text on stderr, exit 2.
    """
    try:
        code = body()
    except HallowError as e:
        _fail(fmt, e)
    except FileNotFoundError as e:
        _fail(fmt, HallowError(f"file not found: {e.filename}", UNAVAILABLE, "HALLOW_NOT_FOUND"))
    except tomllib.TOMLDecodeError as e:
        _fail(fmt, HallowError(f"invalid TOML config: {e}", INVALID_INPUT, "HALLOW_INVALID_CONFIG"))
    except OSError as e:
        # unwritable --output, a directory where a file is expected, ...
        where = f": {e.filename}" if e.filename else ""
        _fail(fmt, HallowError(f"{e.strerror or e}{where}", INVALID_INPUT, "HALLOW_IO_ERROR"))
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        _fail(
            fmt,
            HallowError(f"invalid config: {where}: {first['msg']}", code="HALLOW_INVALID_CONFIG"),
        )
    if code:
        raise typer.Exit(code=code)


def _fail(fmt: str | None, err: HallowError) -> None:
    from hallow.output.envelope import ErrorOutput

    if fmt == "json":
        doc = ErrorOutput(
            message=err.message, exit_code=err.exit_code, code=err.code, help=err.help
        )
        typer.echo(doc.model_dump_json(indent=2, exclude_none=True))
    else:
        typer.echo(f"error: {err.message}", err=True)
        if err.help:
            typer.echo(err.help, err=True)
    raise typer.Exit(code=err.exit_code)


def _check_format(fmt: str | None, allowed: tuple[str, ...]) -> str:
    fmt = fmt or "human"
    if fmt not in allowed:
        raise HallowError(
            f"unknown format '{fmt}'; expected one of: {', '.join(allowed)}",
            code="HALLOW_INVALID_FORMAT",
        )
    return fmt


def _load_config(root: Path | None, config_path: Path | None, overrides: dict | None = None):
    from hallow.config import load_config

    if config_path is not None and not config_path.exists():
        raise HallowError(
            f"config file not found: {config_path}", UNAVAILABLE, "HALLOW_CONFIG_NOT_FOUND"
        )
    if root is not None and not root.is_dir():
        raise HallowError(f"root is not a directory: {root}", code="HALLOW_INVALID_ROOT")
    return load_config(root=root, config_path=config_path, overrides=overrides or None)


def _write(text: str, output_file: Path | None) -> None:
    if output_file:
        output_file.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    else:
        typer.echo(text)


def _status(message: str, quiet: bool) -> None:
    if not quiet:
        typer.echo(message, err=True)


def _exit_for(gates: dict) -> int:
    return ISSUES if any(g.status == "fail" and g.enforced for g in gates.values()) else 0


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


# ── Commands ──


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    root: RootOpt = None,
    config: ConfigOpt = None,
    format: str | None = _format_opt(CHECK_FORMATS),
    output_file: OutputOpt = None,
    ci: CiOpt = False,
    fail_on_issues: FailOnIssuesOpt = False,
    quiet: QuietOpt = False,
    changed_since: ChangedSinceOpt = None,
    boundaries: BoundariesOpt = None,
    baseline: BaselineOpt = None,
    save_baseline: SaveBaselineOpt = None,
    fail_on_stale_baseline: FailOnStaleOpt = False,
    version: bool | None = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Run the full analysis (same as `hallow check`) when no command is given."""
    if ctx.invoked_subcommand is not None:
        # Options given before a command belong to the bare `hallow` run and
        # would be silently dropped; refuse instead (`hallow --boundaries
        # layered check` used to run check without boundaries).
        given = [
            p.opts[0]
            for p in ctx.command.params
            if p.name not in ("version",)
            and ctx.get_parameter_source(p.name) is not None
            and ctx.get_parameter_source(p.name).name == "COMMANDLINE"
        ]
        if given:
            raise typer.BadParameter(
                f"{', '.join(given)} given before '{ctx.invoked_subcommand}'; "
                f"put options after the command: hallow {ctx.invoked_subcommand} {given[0]} ...",
                param_hint="options",
            )
        return
    _run_check(
        root, config, format, output_file, ci, fail_on_issues, quiet, changed_since,
        boundaries, baseline, save_baseline, fail_on_stale_baseline,
    )  # fmt: skip


@app.command()
def check(
    root: RootOpt = None,
    config: ConfigOpt = None,
    format: str | None = _format_opt(CHECK_FORMATS),
    output_file: OutputOpt = None,
    ci: CiOpt = False,
    fail_on_issues: FailOnIssuesOpt = False,
    quiet: QuietOpt = False,
    changed_since: ChangedSinceOpt = None,
    boundaries: BoundariesOpt = None,
    baseline: BaselineOpt = None,
    save_baseline: SaveBaselineOpt = None,
    fail_on_stale_baseline: FailOnStaleOpt = False,
) -> None:
    """Run the full analysis: dead code, dependencies, cycles, duplication, complexity, security.

    Exits 1 when error-severity findings remain (warn-severity too with --fail-on-issues).
    """
    _run_check(
        root, config, format, output_file, ci, fail_on_issues, quiet, changed_since,
        boundaries, baseline, save_baseline, fail_on_stale_baseline,
    )  # fmt: skip


def _ci_defaults(fmt: str | None, ci: bool, fail_on_issues: bool, quiet: bool):
    if ci:
        return fmt or "sarif", True, True
    return fmt, fail_on_issues, quiet


def _run_check(
    root: Path | None,
    config_path: Path | None,
    fmt: str | None,
    output_file: Path | None,
    ci: bool,
    fail_on_issues: bool,
    quiet: bool,
    changed_since: str | None,
    boundaries: str | None,
    baseline_path: Path | None,
    save_baseline_path: Path | None,
    fail_on_stale_baseline: bool,
) -> None:
    fmt, fail_on_issues, quiet = _ci_defaults(fmt, ci, fail_on_issues, quiet)

    def body() -> int:
        from hallow.core import analyze
        from hallow.output.envelope import findings_gate, stale_baseline_gate

        start = time.monotonic()
        out_fmt = _check_format(fmt, CHECK_FORMATS)
        overrides: dict = {}
        if boundaries:
            overrides["boundaries"] = {"preset": boundaries}
        if changed_since:
            overrides["changed_since"] = changed_since
        cfg = _load_config(root, config_path, overrides)

        results = analyze(cfg)
        results = _apply_baseline(results, baseline_path, save_baseline_path, quiet)

        gates = {"findings": findings_gate(results.findings, fail_on_issues)}
        if baseline_path:
            gates["stale-baseline"] = stale_baseline_gate(
                results.baseline_staleness, enforced=fail_on_stale_baseline
            )
        _emit_results(
            results, out_fmt, output_file, "check", _elapsed_ms(start), gates,
            _context(cfg, config_path),
        )  # fmt: skip
        return _exit_for(gates)

    _guarded(fmt, body)


def _apply_baseline(results, baseline_path, save_baseline_path, quiet: bool):
    from hallow.core.baseline import filter_baseline, save_baseline

    if save_baseline_path:
        count = save_baseline(results, save_baseline_path)
        _status(f"Saved baseline with {count} finding(s) to {save_baseline_path}", quiet)

    if baseline_path:
        results = filter_baseline(results, baseline_path)
        st = results.baseline_staleness
        # Printed even under --quiet: a baseline that silently suppresses
        # nothing (or less than it used to) is the failure --quiet would hide.
        if st and st.unrecognised_format:
            typer.echo(
                f"warning: {baseline_path} is not a hallow check baseline; it suppressed nothing",
                err=True,
            )
        elif st and st.warning != "none":
            typer.echo(
                f"warning: baseline {baseline_path} is stale: {st.stale_entries} of "
                f"{st.baseline_entries} entries match no finding "
                f"(prune with `hallow baselines prune --baseline {baseline_path}`)",
                err=True,
            )
    return results


def _context(cfg, config: Path | None):
    """The run's effective project root and config as absolute paths, so a
    suggested command analyses the same project from any directory (the root
    is included even when it was found by walking up from the cwd)."""
    from hallow.output.envelope import RunContext

    return RunContext(
        root=str(cfg.root.resolve()),
        config=str(config.resolve()) if config is not None else None,
    )


def _emit_results(
    results, fmt: str, output_file: Path | None, kind, elapsed_ms, gates, ctx=None
) -> None:
    from hallow.output import format_results
    from hallow.output.envelope import check_output

    if fmt == "json":
        doc = check_output(results, kind, elapsed_ms, gates, ctx)
        _write(doc.model_dump_json(indent=2), output_file)
        return

    text_formats = {"sarif", "markdown", "codeclimate", "github"}
    cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
    with cm as fh:
        output = format_results(results, fmt=fmt, file=fh)
        if fmt in text_formats and not fh and output:
            typer.echo(output)


@app.command()
def health(
    root: RootOpt = None,
    config: ConfigOpt = None,
    format: str | None = _format_opt(DOC_FORMATS),
    score: bool = typer.Option(False, "--score", help="Show project-level health score summary"),
    output_file: OutputOpt = None,
) -> None:
    """Compute project health score and complexity metrics."""

    def body() -> int:
        from hallow.core.discovery import discover_python_files
        from hallow.core.health import compute_file_health, compute_project_health
        from hallow.extract import extract_modules_parallel
        from hallow.output.envelope import HealthOutput, NextStep
        from hallow.output.formatters import format_health

        start = time.monotonic()
        fmt = _check_format(format, DOC_FORMATS)
        cfg = _load_config(root, config)
        root_path = cfg.root.resolve()
        modules = extract_modules_parallel(discover_python_files(cfg), root_path)

        project = compute_project_health(modules, cfg)
        file_healths = [
            compute_file_health(p, m, cfg)
            for p, m in sorted(modules.items())
            if not m.is_test and not m.is_conftest
        ]

        if fmt == "json":
            doc = HealthOutput(
                elapsed_ms=_elapsed_ms(start),
                project=project,
                files=file_healths,
                next_steps=[]
                if not project.hotspots
                else [
                    NextStep(
                        id="check-complexity",
                        command=_context(cfg, config).command("check", "--format", "json"),
                        reason=f"{len(project.hotspots)} file(s) hold complexity hotspots",
                    )
                ],
            )
            _write(doc.model_dump_json(indent=2), output_file)
            return 0

        cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
        with cm as fh:
            format_health(project, file_healths, fmt=fmt, score_only=score, file=fh)
        return 0

    _guarded(format, body)


@app.command()
def dupes(
    root: RootOpt = None,
    config: ConfigOpt = None,
    mode: str = typer.Option(
        "mild", "--mode", "-m", help="Detection mode: strict, mild, weak, semantic"
    ),
    min_tokens: int = typer.Option(
        50, "--min-tokens", help="Minimum token count for a duplicate group"
    ),
    min_lines: int = typer.Option(
        5, "--min-lines", help="Minimum line count for a duplicate fragment"
    ),
    format: str | None = _format_opt(DOC_FORMATS),
    output_file: OutputOpt = None,
    fail_on_issues: FailOnIssuesOpt = False,
) -> None:
    """Detect duplicate code blocks.

    duplicate-code is warn-severity by default, so this exits 0 unless the rule is
    set to error or --fail-on-issues is given.
    """

    def body() -> int:
        from hallow.core.discovery import discover_python_files
        from hallow.core.duplicates import detect_duplicates
        from hallow.core.suppressions import apply_suppressions
        from hallow.extract import extract_modules_parallel
        from hallow.identity import stamp_identities
        from hallow.output.envelope import DupesOutput, finding_output, findings_gate
        from hallow.output.formatters import format_dupes

        start = time.monotonic()
        fmt = _check_format(format, DOC_FORMATS)
        if mode not in DUPE_MODES:
            raise HallowError(
                f"unknown mode '{mode}'; expected one of: strict, mild, weak, semantic",
                code="HALLOW_INVALID_MODE",
            )
        overrides = {"duplicates": {"mode": mode, "min_tokens": min_tokens, "min_lines": min_lines}}
        cfg = _load_config(root, config, overrides)
        files = discover_python_files(cfg)
        groups, findings = detect_duplicates(files, cfg.root.resolve(), cfg)
        # the same `# hallow-ignore` handling as the full analysis, so a clone
        # suppressed there cannot fail `dupes --fail-on-issues`
        modules = extract_modules_parallel(files, cfg.root.resolve())
        findings = apply_suppressions(findings, modules, cfg, report_stale=False)
        stamp_identities(findings)
        gates = {"findings": findings_gate(findings, fail_on_issues)}

        if fmt == "json":
            doc = DupesOutput(
                elapsed_ms=_elapsed_ms(start),
                mode=mode,
                clone_groups=groups,
                findings=[finding_output(f) for f in findings],
                gate_outcomes=gates,
            )
            _write(doc.model_dump_json(indent=2), output_file)
        else:
            cm = Path(output_file).open("w") if output_file else contextlib.nullcontext()
            with cm as fh:
                format_dupes(groups, fmt=fmt, file=fh)
        return _exit_for(gates)

    _guarded(format, body)


@app.command()
def fix(
    root: RootOpt = None,
    config: ConfigOpt = None,
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Preview changes without applying (default: dry run)"
    ),
) -> None:
    """Auto-fix unused imports, dead files, and unused dependencies."""

    def body() -> int:
        _run_fix(root, config, dry_run)
        return 0

    _guarded(None, body)


@app.command()
def audit(
    root: RootOpt = None,
    config: ConfigOpt = None,
    changed_since: str = typer.Option(
        "origin/main", "--changed-since", help="Git ref to diff against (default: origin/main)"
    ),
    format: str | None = _format_opt(CHECK_FORMATS),
    output_file: OutputOpt = None,
    ci: CiOpt = False,
    fail_on_issues: FailOnIssuesOpt = False,
    quiet: QuietOpt = False,
    baseline: BaselineOpt = None,
    save_baseline: SaveBaselineOpt = None,
) -> None:
    """PR-scoped audit — only flag issues in changed files."""
    _run_check(
        root, config, format, output_file, ci, fail_on_issues, quiet, changed_since,
        None, baseline, save_baseline, False,
    )  # fmt: skip


def _run_fix(root: Path | None, config_path: Path | None, dry_run: bool) -> None:
    from rich.console import Console

    from hallow.core import analyze
    from hallow.core.fixer import apply_fixes

    console = Console(highlight=False)

    cfg = _load_config(root, config_path)
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


@app.command()
def security(
    root: RootOpt = None,
    config: ConfigOpt = None,
    format: str | None = _format_opt(CHECK_FORMATS),
    output_file: OutputOpt = None,
    ci: CiOpt = False,
    fail_on_issues: FailOnIssuesOpt = False,
    quiet: QuietOpt = False,
) -> None:
    """Scan for hardcoded secrets and taint sinks. Exits 1 on error-severity findings."""
    fmt, fail_on_issues, quiet = _ci_defaults(format, ci, fail_on_issues, quiet)

    def body() -> int:
        from hallow.core.discovery import discover_python_files
        from hallow.core.suppressions import apply_suppressions
        from hallow.extract import extract_modules_parallel
        from hallow.identity import stamp_identities
        from hallow.output.envelope import findings_gate
        from hallow.security import detect_hardcoded_secrets, detect_taint_sinks
        from hallow.types import AnalysisResults

        start = time.monotonic()
        out_fmt = _check_format(fmt, CHECK_FORMATS)
        cfg = _load_config(root, config)
        root_path = cfg.root.resolve()
        files = discover_python_files(cfg)
        modules = extract_modules_parallel(files, root_path)

        findings = []
        findings.extend(detect_hardcoded_secrets(modules, root_path, cfg))
        findings.extend(detect_taint_sinks(modules, root_path, cfg))
        findings = apply_suppressions(findings, modules, cfg, report_stale=False)
        stamp_identities(findings)

        results = AnalysisResults(findings=findings, total_files_scanned=len(files))
        results.compute_totals()
        results.sort()

        gates = {"findings": findings_gate(results.findings, fail_on_issues)}
        _emit_results(
            results, out_fmt, output_file, "security", _elapsed_ms(start), gates,
            _context(cfg, config),
        )  # fmt: skip
        return _exit_for(gates)

    _guarded(fmt, body)


@app.command()
def mcp(
    stdio: bool = typer.Option(
        True, "--stdio/--no-stdio", help="Run MCP server over stdio (default)"
    ),
) -> None:
    """Start the MCP server for agent-driven analysis."""
    from hallow.mcp import create_mcp_app

    server = create_mcp_app()
    server.run_stdio()


@app.command()
def schema(
    output_schema: bool = typer.Option(
        False,
        "--output-schema",
        help="Print the JSON Schema of every --format json document instead",
    ),
) -> None:
    """Print a JSON description of hallow: commands, flags, formats, exit codes, rules."""
    from hallow.cli.manifest import build_manifest
    from hallow.output.envelope import output_json_schema

    doc = output_json_schema() if output_schema else build_manifest(app)
    typer.echo(json.dumps(doc, indent=2))


baselines_app = typer.Typer(help="Maintain baseline files.", no_args_is_help=True)
app.add_typer(baselines_app, name="baselines")


@baselines_app.command("prune")
def baselines_prune(
    baseline: Path = typer.Option(..., "--baseline", help="Baseline file to prune"),
    check: bool = typer.Option(
        False, "--check", help="Change nothing; exit 1 if entries could be pruned"
    ),
    root: RootOpt = None,
    config: ConfigOpt = None,
    format: str | None = _format_opt(DOC_FORMATS),
) -> None:
    """Remove baseline entries that no current finding matches. Never adds entries.

    Exit 0 when done, 1 with --check when entries could be pruned, 2 when the file
    is missing or is not a hallow check baseline.
    """

    def body() -> int:
        from hallow.core import analyze
        from hallow.core.baseline import prune_baseline
        from hallow.output.envelope import PruneFile, PruneOutput, prune_gate

        start = time.monotonic()
        fmt = _check_format(format, DOC_FORMATS)
        cfg = _load_config(root, config)
        item = PruneFile(**prune_baseline(analyze(cfg), baseline, check=check))

        if item.status in ("refused", "error"):
            # exit 2 is invalid input: the error document, not a prune document
            raise HallowError(
                f"{baseline}: {item.reason}",
                code="HALLOW_BASELINE_REFUSED" if item.status == "refused" else "HALLOW_NOT_FOUND",
            )
        if fmt == "json":
            doc = PruneOutput(
                elapsed_ms=_elapsed_ms(start),
                check=check,
                files=[item],
                gate_outcomes={"prune-check": prune_gate(item.status, check)},
            )
            typer.echo(doc.model_dump_json(indent=2))
        else:
            before, after = item.entries_before or 0, item.entries_after or 0
            typer.echo(f"{baseline}: {item.status} ({before - after} stale of {before} entries)")
            for r in item.removed:
                typer.echo(f"  - {r.key}" + (f" (x{r.count})" if r.count > 1 else ""))

        return ISSUES if check and item.status == "would-prune" else 0

    _guarded(format, body)


if __name__ == "__main__":
    app()
