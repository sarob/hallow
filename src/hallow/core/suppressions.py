"""Apply `# hallow-ignore` comments and report the ones that suppress nothing."""

from __future__ import annotations

from hallow.config.loader import HallowConfig
from hallow.types import Finding, Location, ModuleInfo, RuleId, Severity, Suppression

_KNOWN_RULES = {r.value for r in RuleId}


def _covers(s: Suppression, finding: Finding) -> bool:
    if s.rules and finding.rule.value not in s.rules:
        return False
    if s.kind == "file":
        return True
    return s.target_line is not None and s.target_line == finding.location.line


def apply_suppressions(
    findings: list[Finding],
    modules: dict[str, ModuleInfo],
    config: HallowConfig,
    report_stale: bool = True,
) -> list[Finding]:
    """Drop suppressed findings; with `report_stale`, add a finding per unused suppression.

    Only pass `report_stale` when every enabled rule ran — a partial run (e.g.
    `hallow security`) would see every dead-code suppression as unused.
    """
    used: set[tuple[str, int]] = set()
    kept: list[Finding] = []

    for f in findings:
        module = modules.get(f.location.file)
        hit = None
        if module is not None:
            hit = next((s for s in module.suppressions if _covers(s, f)), None)
        if hit is None:
            kept.append(f)
        else:
            used.add((f.location.file, hit.line))

    if not report_stale:
        return kept

    severity = config.rules.severity_for(RuleId.STALE_SUPPRESSION)
    if severity == Severity.OFF:
        return kept

    def diagnostics() -> list[tuple[str, Suppression, Finding]]:
        """Stale-suppression findings for the current `used` set.

        A suppression that hides nothing is stale; one that names an unknown
        rule is reported whether or not it is used (a typo next to a valid
        rule must not hide behind it). A suppression naming only disabled
        rules is inert, not stale.
        """
        out: list[tuple[str, Suppression, Finding]] = []
        for path, module in sorted(modules.items()):
            for s in module.suppressions:
                unknown = [r for r in s.rules if r not in _KNOWN_RULES]
                enabled = [
                    r
                    for r in s.rules
                    if r in _KNOWN_RULES and config.rules.severity_for(RuleId(r)) != Severity.OFF
                ]
                is_used = (path, s.line) in used
                if is_used and not unknown:
                    continue
                if not is_used and s.rules and not enabled and not unknown:
                    continue
                directive = {"line": "hallow-ignore", "next-line": "hallow-ignore-next-line"}.get(
                    s.kind, "hallow-ignore-file"
                )
                if is_used:
                    message = f"'{directive}' names unknown rule(s): {', '.join(unknown)}"
                else:
                    what = ", ".join(s.rules) or "all rules"
                    message = f"'{directive}' ({what}) suppresses no finding"
                    if unknown:
                        message += f"; unknown rule(s): {', '.join(unknown)}"
                finding = Finding(
                    rule=RuleId.STALE_SUPPRESSION,
                    severity=severity,
                    message=message,
                    location=Location(file=path, line=s.line),
                    symbol=f"{s.kind}:{','.join(sorted(s.rules))}",
                    suggestion="Remove the suppression comment, or fix the rule name",
                )
                out.append((path, s, finding))
        return out

    def cover(path: str, s: Suppression, finding: Finding) -> Suppression | None:
        # never the suppression the diagnostic is about: it cannot hide itself
        return next(
            (o for o in modules[path].suppressions if o is not s and _covers(o, finding)), None
        )

    # stale-suppression findings are suppressible like any other. Hiding one
    # makes the covering suppression used, which can change the diagnostics,
    # so recompute until nothing changes.
    changed = True
    while changed:
        changed = False
        for path, s, finding in diagnostics():
            o = cover(path, s, finding)
            if o is not None and (path, o.line) not in used:
                used.add((path, o.line))
                changed = True

    kept.extend(f for path, s, f in diagnostics() if cover(path, s, f) is None)
    return kept
