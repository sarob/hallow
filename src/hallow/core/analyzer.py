"""Analysis orchestrator — runs the full pipeline."""

from __future__ import annotations

from hallow.config.loader import HallowConfig
from hallow.core.boundaries import detect_boundary_violations
from hallow.core.detectors import (
    detect_circular_imports,
    detect_unlisted_dependencies,
    detect_unused_dependencies,
    detect_unused_exports,
    detect_unused_files,
    detect_unused_imports,
)
from hallow.core.discovery import discover_python_files
from hallow.core.duplicates import detect_duplicates
from hallow.core.health import compute_project_health, detect_high_complexity
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph
from hallow.plugins import load_plugins
from hallow.security import detect_hardcoded_secrets, detect_taint_sinks
from hallow.types import AnalysisResults


def analyze(config: HallowConfig) -> AnalysisResults:
    root = config.root.resolve()

    files = discover_python_files(config)
    if not files:
        return AnalysisResults(total_files_scanned=0)

    modules = extract_modules_parallel(files, root)
    if not modules:
        return AnalysisResults(total_files_scanned=len(files))

    graph = ModuleGraph(modules, root)
    plugins = load_plugins(root)

    findings = []
    findings.extend(detect_unused_files(graph, config))
    findings.extend(detect_unused_imports(graph, config))
    findings.extend(detect_unused_exports(graph, config))
    findings.extend(detect_unused_dependencies(graph, config))
    findings.extend(detect_unlisted_dependencies(graph, config))

    cycles = graph.find_cycles()
    cycle_findings = detect_circular_imports(graph, config)
    findings.extend(cycle_findings)

    findings.extend(detect_high_complexity(modules, config))
    findings.extend(detect_boundary_violations(graph, config))

    duplicates, dupe_findings = detect_duplicates(files, root, config)
    findings.extend(dupe_findings)

    findings.extend(detect_hardcoded_secrets(modules, root, config))
    findings.extend(detect_taint_sinks(modules, root, config))

    filtered = _apply_plugin_suppressions(findings, plugins)

    health = compute_project_health(modules, config)

    results = AnalysisResults(
        findings=filtered,
        cycles=cycles,
        duplicates=duplicates,
        health=health,
        total_files_scanned=len(files),
    )
    results.compute_totals()
    results.sort()

    return results


def _apply_plugin_suppressions(
    findings: list,
    plugins,
) -> list:
    if not plugins.plugins:
        return findings

    result = []
    for f in findings:
        suppressed = plugins.suppressed_rules(f.location.file)
        if f.rule.value not in suppressed:
            result.append(f)
    return result
