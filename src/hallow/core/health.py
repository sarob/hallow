"""Health scoring — per-file maintainability index and project-level grade."""

from __future__ import annotations

import math

from hallow.config.loader import HallowConfig
from hallow.types import (
    FileHealth,
    Finding,
    FunctionComplexity,
    Location,
    ModuleInfo,
    ProjectHealth,
    RuleId,
    Severity,
)


def compute_file_health(
    path: str,
    module: ModuleInfo,
    config: HallowConfig,
) -> FileHealth:
    funcs = module.functions
    loc = module.line_count or 1

    if not funcs:
        return FileHealth(
            path=path,
            lines_of_code=loc,
            maintainability_index=100.0,
        )

    cc_values = [f.cyclomatic for f in funcs]
    cog_values = [f.cognitive for f in funcs]

    cc_avg = sum(cc_values) / len(cc_values)
    cc_max = max(cc_values)
    cog_avg = sum(cog_values) / len(cog_values)
    cog_max = max(cog_values)

    mi = _maintainability_index(cc_avg, loc)

    hotspots = [
        f.name
        for f in funcs
        if f.cyclomatic > config.health.max_cyclomatic or f.cognitive > config.health.max_cognitive
    ]

    return FileHealth(
        path=path,
        cyclomatic_avg=round(cc_avg, 2),
        cyclomatic_max=cc_max,
        cognitive_avg=round(cog_avg, 2),
        cognitive_max=cog_max,
        maintainability_index=round(mi, 1),
        lines_of_code=loc,
        hotspot_functions=hotspots,
    )


def compute_project_health(
    modules: dict[str, ModuleInfo],
    config: HallowConfig,
) -> ProjectHealth:
    file_healths: list[FileHealth] = []
    total_functions = 0
    total_lines = 0

    for path, module in sorted(modules.items()):
        if module.is_test or module.is_conftest:
            continue
        fh = compute_file_health(path, module, config)
        file_healths.append(fh)
        total_functions += len(module.functions)
        total_lines += module.line_count

    if not file_healths:
        return ProjectHealth(
            score=100,
            grade="A",
            total_files=0,
            total_functions=0,
            total_lines=0,
        )

    mi_values = [fh.maintainability_index for fh in file_healths]
    cc_values = [fh.cyclomatic_avg for fh in file_healths if fh.cyclomatic_avg > 0]
    cog_values = [fh.cognitive_avg for fh in file_healths if fh.cognitive_avg > 0]

    mi_avg = sum(mi_values) / len(mi_values)
    cc_avg = sum(cc_values) / len(cc_values) if cc_values else 0.0
    cog_avg = sum(cog_values) / len(cog_values) if cog_values else 0.0

    hotspot_files = [fh for fh in file_healths if fh.hotspot_functions]
    hotspot_files.sort(key=lambda h: h.cyclomatic_max, reverse=True)

    score = int(min(100, max(0, mi_avg)))
    grade = _score_to_grade(score)

    return ProjectHealth(
        score=score,
        grade=grade,
        total_files=len(file_healths),
        total_functions=total_functions,
        total_lines=total_lines,
        cyclomatic_avg=round(cc_avg, 2),
        cognitive_avg=round(cog_avg, 2),
        maintainability_avg=round(mi_avg, 1),
        hotspots=hotspot_files[:10],
    )


def detect_high_complexity(
    modules: dict[str, ModuleInfo],
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.HIGH_COMPLEXITY)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []
    max_cc = config.health.max_cyclomatic
    max_cog = config.health.max_cognitive

    for path, module in modules.items():
        if module.is_test or module.is_conftest:
            continue
        for func in module.functions:
            _check_complexity(func, path, max_cc, max_cog, severity, findings)

    return findings


def _check_complexity(
    func: FunctionComplexity,
    path: str,
    max_cc: int,
    max_cog: int,
    severity: Severity,
    findings: list[Finding],
) -> None:
    if func.cyclomatic > max_cc:
        findings.append(
            Finding(
                rule=RuleId.HIGH_COMPLEXITY,
                severity=severity,
                message=(
                    f"'{func.name}' has cyclomatic complexity {func.cyclomatic} (max {max_cc})"
                ),
                location=Location(
                    file=path,
                    line=func.line,
                    end_line=func.end_line,
                ),
                suggestion="Break this function into smaller pieces",
                metadata={
                    "kind": "cyclomatic",
                    "value": func.cyclomatic,
                    "threshold": max_cc,
                },
            )
        )
    if func.cognitive > max_cog:
        findings.append(
            Finding(
                rule=RuleId.HIGH_COMPLEXITY,
                severity=severity,
                message=(
                    f"'{func.name}' has cognitive complexity {func.cognitive} (max {max_cog})"
                ),
                location=Location(
                    file=path,
                    line=func.line,
                    end_line=func.end_line,
                ),
                suggestion="Reduce nesting and simplify control flow",
                metadata={
                    "kind": "cognitive",
                    "value": func.cognitive,
                    "threshold": max_cog,
                },
            )
        )


def _maintainability_index(avg_cyclomatic: float, loc: int) -> float:
    ln_loc = math.log(max(loc, 1))
    mi = 171 - 5.2 * ln_loc - 0.23 * avg_cyclomatic - 16.2 * ln_loc
    return max(0.0, mi * 100 / 171)


def _score_to_grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"
