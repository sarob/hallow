"""The audit gate — fail a change only on the findings it introduces.

After fallow's `audit` (crates/api/src/audit_run): analyse the working tree,
keep the findings that touch a changed file, then re-analyse the base commit
in a temporary worktree with the same config and compare line-free canonical
keys (hallow.identity). A finding whose key has no remaining occurrence at
the base is *introduced*; one that existed there is *inherited*. Renamed files
are mapped old -> new first, so a pure `git mv` stays inherited.

- complexity: introduced if the function is new, or the metric it exceeds is
  higher than at the base
- duplication: an introduced clone group none of whose fragments contains an
  added line is demoted to inherited (`demotion_reason: no-added-lines`)
- a change that touches no Python file and no dependency or hallow config
  skips the base analysis; every finding counts as inherited

Verdict: `fail` if a considered finding is error-severity, `warn` if one is
warn-severity, else `pass`. Under `--gate new-only` (default) only introduced
findings are considered; under `--gate all`, every finding in changed files
is, and the base analysis is skipped.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from hallow.config.loader import HallowConfig
from hallow.core.analyzer import analyze
from hallow.core.gitutil import BaseRef, ChangeSet, collect_changes, git, repo_top, worktree
from hallow.identity import canonical_key
from hallow.types import Finding, RuleId, Severity

GATES = ("new-only", "all")

_DEAD_CODE = {
    RuleId.UNUSED_FILES,
    RuleId.UNUSED_IMPORTS,
    RuleId.UNUSED_FUNCTIONS,
    RuleId.UNUSED_CLASSES,
    RuleId.UNUSED_VARIABLES,
    RuleId.UNUSED_DEPENDENCIES,
    RuleId.UNLISTED_DEPENDENCIES,
    RuleId.UNUSED_ALL_ENTRIES,
    RuleId.CIRCULAR_DEPENDENCIES,
    RuleId.STALE_SUPPRESSION,
}


def category(rule: RuleId) -> str:
    if rule in _DEAD_CODE:
        return "dead_code"
    if rule == RuleId.HIGH_COMPLEXITY:
        return "complexity"
    if rule == RuleId.DUPLICATE_CODE:
        return "duplication"
    return "other"


@dataclass
class AuditFinding:
    finding: Finding
    introduced: bool | None  # None under --gate all (no attribution)
    demotion_reason: str | None = None


@dataclass
class AuditResult:
    verdict: str
    gate: str
    base: BaseRef
    head_sha: str | None
    changed_files: list[str]
    findings: list[AuditFinding]
    base_snapshot_skipped: bool
    total_files_scanned: int
    attribution: dict[str, int | str] = field(default_factory=dict)

    @property
    def considered(self) -> list[Finding]:
        """The findings the verdict rests on."""
        return [a.finding for a in self.findings if a.introduced is not False]


def finding_files(f: Finding) -> set[str]:
    """Every file a finding concerns: its location, clone fragments, cycle members."""
    files = {f.location.file}
    for item in f.metadata.get("fragment_ranges", []):
        files.add(item[0])
    if f.rule == RuleId.CIRCULAR_DEPENDENCIES and f.symbol:
        files.update(m for m in f.symbol.split("|") if m)
    return files


def _needs_base(changes: ChangeSet) -> bool:
    return any(
        p.endswith(".py")
        or p in ("pyproject.toml", ".hallowrc.toml")
        or (p.rsplit("/", 1)[-1].startswith("requirements") and p.endswith(".txt"))
        for p in changes.files
    )


def _verdict(considered: list[Finding]) -> str:
    if any(f.severity == Severity.ERROR for f in considered):
        return "fail"
    if any(f.severity == Severity.WARN for f in considered):
        return "warn"
    return "pass"


def _metric(f: Finding) -> int:
    value = f.metadata.get("value", 0)
    return int(value) if isinstance(value, int | float) else 0


def _touches_added_line(f: Finding, changes: ChangeSet) -> bool:
    for file, start, end in f.metadata.get("fragment_ranges", []):
        added = changes.added_lines.get(file, set())
        if any(start <= n <= end for n in added):
            return True
    return False


def match_complexity(base_values: list[int], head_values: list[int]) -> list[bool]:
    """For findings sharing one complexity key: is each head occurrence introduced?

    Each base occurrence covers at most one head occurrence. Highest head
    values are matched first, each to the smallest remaining base value at
    least as high (inherited). A head value above every remaining base value
    is a rise (introduced) and consumes the highest remaining base value; a
    head occurrence with no base value left is new (introduced). Returned in
    the order of `head_values`.
    """
    pool = sorted(base_values)
    result: dict[int, bool] = {}
    for i in sorted(range(len(head_values)), key=lambda i: -head_values[i]):
        h = head_values[i]
        fit = next((j for j, b in enumerate(pool) if b >= h), None)
        if fit is not None:
            pool.pop(fit)
            result[i] = False
        else:
            if pool:
                pool.pop()
            result[i] = True
    return [result[i] for i in range(len(head_values))]


def run_audit(cfg: HallowConfig, base: BaseRef, gate: str, baseline_path: Path | None = None):
    """Run the audit. `base` comes from gitutil.resolve_base.

    Attribution runs on the unfiltered head findings; a `--baseline` is
    applied afterwards and consumes inherited occurrences before introduced
    ones, so baseline credit never lets an added occurrence pass as inherited.
    """
    root = cfg.root.resolve()
    top = repo_top(root)
    changes = collect_changes(top, root, base.sha)
    head_sha = git(top, "rev-parse", "HEAD").strip() or None

    head = analyze(cfg)
    scoped = [f for f in head.findings if finding_files(f) & changes.files]

    skipped = gate == "all" or not _needs_base(changes)
    audited: list[AuditFinding] = []
    if gate == "all":
        audited = [AuditFinding(f, None) for f in scoped]
    elif skipped:
        audited = [AuditFinding(f, False) for f in scoped]
    else:
        base_findings = _analyze_base(cfg, top, root, base.sha)
        remaining: Counter[str] = Counter()
        base_metric: dict[str, list[int]] = defaultdict(list)
        for bf in base_findings:
            key = canonical_key(bf, changes.renames)
            if bf.rule == RuleId.HIGH_COMPLEXITY:
                base_metric[key].append(_metric(bf))
            else:
                remaining[key] += 1

        ordered = sorted(scoped, key=lambda x: (x.location.file, x.location.line))
        complexity: dict[str, list[Finding]] = defaultdict(list)
        verdicts: dict[int, AuditFinding] = {}
        for f in ordered:
            if f.rule == RuleId.HIGH_COMPLEXITY:
                complexity[f.key].append(f)
            elif remaining[f.key] > 0:
                remaining[f.key] -= 1
                verdicts[id(f)] = AuditFinding(f, False)
            elif f.rule == RuleId.DUPLICATE_CODE and not _touches_added_line(f, changes):
                verdicts[id(f)] = AuditFinding(f, False, "no-added-lines")
            else:
                verdicts[id(f)] = AuditFinding(f, True)
        for key, group in complexity.items():
            flags = match_complexity(base_metric.get(key, []), [_metric(f) for f in group])
            for f, introduced in zip(group, flags, strict=True):
                verdicts[id(f)] = AuditFinding(f, introduced)
        audited = [verdicts[id(f)] for f in ordered]

    if baseline_path is not None:
        audited = _apply_baseline(audited, baseline_path)

    result = AuditResult(
        verdict="pass",
        gate=gate,
        base=base,
        head_sha=head_sha,
        changed_files=sorted(changes.files),
        findings=audited,
        base_snapshot_skipped=skipped,
        total_files_scanned=head.total_files_scanned,
    )
    result.verdict = _verdict(result.considered)
    result.attribution = _attribution(audited, gate)
    return result


def _apply_baseline(audited: list[AuditFinding], baseline_path: Path) -> list[AuditFinding]:
    """Drop baselined findings, spending each entry on an inherited occurrence first."""
    from hallow.core.baseline import load_baseline

    remaining = Counter(load_baseline(baseline_path).entries)
    keep: list[AuditFinding] = []
    # inherited (False) and unattributed (None) before introduced (True)
    for a in sorted(audited, key=lambda a: a.introduced is True):
        if remaining[a.finding.key] > 0:
            remaining[a.finding.key] -= 1
        else:
            keep.append(a)
    order = {id(a): i for i, a in enumerate(audited)}
    return sorted(keep, key=lambda a: order[id(a)])


def _analyze_base(cfg: HallowConfig, top: Path, root: Path, sha: str) -> list[Finding]:
    """Findings at the base commit, analysed with the head's config."""
    sub = root.relative_to(top)
    with worktree(top, sha) as wt:
        base_root = wt / sub
        if not base_root.is_dir():
            return []  # the analysed directory is new in this change
        return analyze(cfg.model_copy(update={"root": base_root})).findings


def _attribution(audited: list[AuditFinding], gate: str) -> dict[str, int | str]:
    out: dict[str, int | str] = {"gate": gate}
    for cat in ("dead_code", "complexity", "duplication", "other"):
        out[f"{cat}_introduced"] = 0
        out[f"{cat}_inherited"] = 0
    out["duplication_demoted"] = 0
    for a in audited:
        if a.introduced is None:
            continue
        cat = category(a.finding.rule)
        out[f"{cat}_{'introduced' if a.introduced else 'inherited'}"] += 1  # type: ignore[operator]
        if a.demotion_reason:
            out["duplication_demoted"] += 1  # type: ignore[operator]
    return out
