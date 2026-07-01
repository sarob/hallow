"""Analysis orchestrator — runs the full pipeline."""

from __future__ import annotations

from hallow.config.loader import HallowConfig
from hallow.core.detectors import (
    detect_circular_imports,
    detect_unlisted_dependencies,
    detect_unused_dependencies,
    detect_unused_exports,
    detect_unused_files,
    detect_unused_imports,
)
from hallow.core.discovery import discover_python_files
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph
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

    findings = []
    findings.extend(detect_unused_files(graph, config))
    findings.extend(detect_unused_imports(graph, config))
    findings.extend(detect_unused_exports(graph, config))
    findings.extend(detect_unused_dependencies(graph, config))
    findings.extend(detect_unlisted_dependencies(graph, config))

    cycles = graph.find_cycles()
    cycle_findings = detect_circular_imports(graph, config)
    findings.extend(cycle_findings)

    results = AnalysisResults(
        findings=findings,
        cycles=cycles,
        total_files_scanned=len(files),
    )
    results.compute_totals()
    results.sort()

    return results
