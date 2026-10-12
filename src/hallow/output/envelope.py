"""Typed JSON output contract — one document per command (after fallow's output envelope).

Every `--format json` document has a root `kind` naming the command that made
it, a per-kind integer `schema_version`, the tool `version`, `elapsed_ms`,
`gate_outcomes` (which gates ran and whether each fails the run) and
`next_steps` (read-only commands that run as written). Each finding carries
`actions[]`; `auto_fixable` on an action says whether `hallow fix` applies it.

`schema_version` moves only when a field is renamed, removed or retyped;
additive changes keep it. `docs/output-schema.json` is generated from these
models (`hallow schema --output-schema`).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from hallow import __version__
from hallow.types import (
    AnalysisResults,
    BaselineStaleness,
    DuplicateGroup,
    FileHealth,
    Finding,
    ImportCycle,
    Location,
    ProjectHealth,
    RuleId,
    Severity,
)

CHECK_SCHEMA_VERSION = 1
HEALTH_SCHEMA_VERSION = 1
DUPES_SCHEMA_VERSION = 1
PRUNE_SCHEMA_VERSION = 1


class Action(BaseModel):
    """Something that resolves a finding. `type` is the discriminator."""

    type: str  # remove-import, delete-file, remove-dependency, remove-export, ...
    auto_fixable: bool
    description: str
    # suppress-line / suppress-file: the comment to add
    comment: str | None = None
    # add-to-config: the config key and the value to add to it
    config_key: str | None = None
    value: str | None = None


class FindingOutput(BaseModel):
    finding_id: str
    rule: RuleId
    severity: Severity
    message: str
    location: Location
    symbol: str | None = None
    suggestion: str | None = None
    actions: list[Action] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NextStep(BaseModel):
    id: str
    command: str
    reason: str


class GateOutcome(BaseModel):
    """A gate fails the run only when `status` is "fail" and `enforced` is true."""

    status: Literal["pass", "warn", "fail", "skipped"]
    enforced: bool
    observed: int | None = None
    threshold: int | None = None


class Summary(BaseModel):
    total_files_scanned: int
    total_issues: int
    errors: int
    warnings: int


class Envelope(BaseModel):
    """Root fields every `--format json` document carries."""

    kind: str
    schema_version: int
    version: str = __version__
    elapsed_ms: int = 0
    # which gates ran; the run exits 1 when one has status "fail" and is enforced
    gate_outcomes: dict[str, GateOutcome] = Field(default_factory=dict)
    # read-only commands, shell-quoted, that run as written from any directory
    next_steps: list[NextStep] = Field(default_factory=list)


class CheckOutput(Envelope):
    kind: Literal["check", "security"]
    schema_version: int = CHECK_SCHEMA_VERSION
    summary: Summary
    findings: list[FindingOutput]
    cycles: list[ImportCycle] = Field(default_factory=list)
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    health: ProjectHealth | None = None
    baseline_staleness: BaselineStaleness | None = None


class HealthOutput(Envelope):
    kind: Literal["health"] = "health"
    schema_version: int = HEALTH_SCHEMA_VERSION
    project: ProjectHealth
    files: list[FileHealth]


class DupesOutput(Envelope):
    kind: Literal["dupes"] = "dupes"
    schema_version: int = DUPES_SCHEMA_VERSION
    mode: str
    clone_groups: list[DuplicateGroup]
    findings: list[FindingOutput]


class PruneRemoved(BaseModel):
    key: str
    count: int


class PruneFile(BaseModel):
    kind: str
    path: str
    status: Literal["pruned", "would-prune", "unchanged", "refused", "error"]
    entries_before: int | None = None
    entries_after: int | None = None
    removed: list[PruneRemoved] = Field(default_factory=list)
    reason: str | None = None


class PruneOutput(Envelope):
    kind: Literal["baselines-prune"] = "baselines-prune"
    schema_version: int = PRUNE_SCHEMA_VERSION
    check: bool
    files: list[PruneFile]


class ErrorOutput(BaseModel):
    """Printed on stdout instead of a document when a `--format json` run fails."""

    error: Literal[True] = True
    message: str
    exit_code: int
    code: str | None = None
    help: str | None = None


OUTPUT_MODELS: list[type[BaseModel]] = [
    CheckOutput,
    HealthOutput,
    DupesOutput,
    PruneOutput,
    ErrorOutput,
]


# ── Actions ──

_FILE_LEVEL = {RuleId.UNUSED_FILES, RuleId.CIRCULAR_DEPENDENCIES, RuleId.BOUNDARY_VIOLATION}
_DEPENDENCY_RULES = {RuleId.UNUSED_DEPENDENCIES, RuleId.UNLISTED_DEPENDENCIES}


def actions_for(finding: Finding) -> list[Action]:
    actions: list[Action] = []
    fix = finding.fix
    if fix is not None:
        kind = fix.kind.replace("_", "-")
        descriptions = {
            "remove-import": f"Remove the unused import '{fix.target}'",
            "delete-file": f"Delete {fix.target}",
            "remove-dependency": f"Remove '{fix.target}' from {finding.location.file}",
            "remove-export": f"Remove '{fix.target}' if nothing outside the project uses it",
            "remove-from-all": f"Remove '{fix.target}' from __all__",
        }
        actions.append(
            Action(
                type=kind,
                auto_fixable=fix.auto_fixable,
                description=descriptions.get(kind, finding.suggestion or kind),
            )
        )

    rule = finding.rule.value
    if finding.rule in _DEPENDENCY_RULES and finding.symbol:
        actions.append(
            Action(
                type="add-to-config",
                auto_fixable=False,
                description=f"Ignore '{finding.symbol}' in dependency checks",
                config_key="ignore_dependencies",
                value=finding.symbol,
            )
        )
    elif finding.rule in _FILE_LEVEL or finding.location.file.endswith((".toml", ".txt")):
        if finding.location.file.endswith(".py"):
            actions.append(
                Action(
                    type="suppress-file",
                    auto_fixable=False,
                    description="Suppress this rule for the whole file",
                    comment=f"# hallow-ignore-file {rule}",
                )
            )
    else:
        actions.append(
            Action(
                type="suppress-line",
                auto_fixable=False,
                description="Suppress this finding on its line",
                comment=f"# hallow-ignore {rule}",
            )
        )
    return actions


def finding_output(finding: Finding) -> FindingOutput:
    return FindingOutput(
        finding_id=finding.finding_id,
        rule=finding.rule,
        severity=finding.severity,
        message=finding.message,
        location=finding.location,
        symbol=finding.symbol,
        suggestion=finding.suggestion,
        actions=actions_for(finding),
        metadata=finding.metadata,
    )


# ── Gates and next steps ──


def findings_gate(findings: list[Finding], fail_on_issues: bool) -> GateOutcome:
    """Error-severity findings fail the run; `--fail-on-issues` counts warnings too."""
    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARN)
    observed = errors + warnings if fail_on_issues else errors
    if observed:
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"
    return GateOutcome(status=status, enforced=True, observed=observed)


def stale_baseline_gate(staleness: BaselineStaleness | None, enforced: bool) -> GateOutcome:
    if staleness is None:
        return GateOutcome(status="skipped", enforced=enforced)
    stale = staleness.stale_entries + (1 if staleness.unrecognised_format else 0)
    return GateOutcome(
        status="fail" if stale else "pass",
        enforced=enforced,
        observed=staleness.stale_entries,
    )


@dataclass(frozen=True)
class RunContext:
    """Where a run looked, so suggested commands look at the same project."""

    root: str | None = None
    config: str | None = None

    def command(self, *args: str) -> str:
        """A shell-quoted `hallow ...` command carrying this run's --root/--config."""
        argv = ["hallow", *args]
        if self.root is not None:
            argv += ["--root", self.root]
        if self.config is not None:
            argv += ["--config", self.config]
        return shlex.join(argv)


def check_next_steps(results: AnalysisResults, ctx: RunContext | None = None) -> list[NextStep]:
    ctx = ctx or RunContext()
    steps: list[NextStep] = []
    if any(f.fix and f.fix.auto_fixable for f in results.findings):
        steps.append(
            NextStep(
                id="preview-fixes",
                command=ctx.command("fix", "--dry-run"),
                reason="Some findings are auto-fixable; preview the changes",
            )
        )
    st = results.baseline_staleness
    if st and st.stale and not st.unrecognised_format:
        steps.append(
            NextStep(
                id="prune-baseline",
                command=ctx.command("baselines", "prune", "--baseline", st.path, "--check"),
                reason=f"{st.stale_entries} baseline entries match no finding",
            )
        )
    if any(f.rule == RuleId.HIGH_COMPLEXITY for f in results.findings):
        steps.append(
            NextStep(
                id="health-hotspots",
                command=ctx.command("health", "--format", "json"),
                reason="See per-file complexity and maintainability",
            )
        )
    return steps


def check_output(
    results: AnalysisResults,
    kind: Literal["check", "security"],
    elapsed_ms: int,
    gate_outcomes: dict[str, GateOutcome] | None = None,
    ctx: RunContext | None = None,
) -> CheckOutput:
    """The check/security document. Without explicit gates, the default
    findings gate is reported (as `hallow check` with no flags would)."""
    if gate_outcomes is None:
        gate_outcomes = {"findings": findings_gate(results.findings, fail_on_issues=False)}
    return CheckOutput(
        kind=kind,
        elapsed_ms=elapsed_ms,
        summary=Summary(
            total_files_scanned=results.total_files_scanned,
            total_issues=results.total_issues,
            errors=results.errors,
            warnings=results.warnings,
        ),
        findings=[finding_output(f) for f in results.findings],
        cycles=results.cycles,
        duplicates=results.duplicates,
        health=results.health,
        baseline_staleness=results.baseline_staleness,
        gate_outcomes=gate_outcomes,
        next_steps=check_next_steps(results, ctx),
    )


def prune_gate(status: str, check: bool) -> GateOutcome:
    """`baselines prune --check` fails (exit 1) when entries could be pruned."""
    return GateOutcome(status="fail" if status == "would-prune" else "pass", enforced=check)


def output_json_schema() -> dict[str, Any]:
    """JSON Schema of every output document, keyed by model name."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "hallow output",
        "description": "Documents hallow writes with --format json, discriminated by `kind`.",
        "oneOf": [{"$ref": f"#/$defs/{m.__name__}"} for m in OUTPUT_MODELS],
        "$defs": _merged_defs(),
    }


def _merged_defs() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for model in OUTPUT_MODELS:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
    return dict(sorted(defs.items()))
