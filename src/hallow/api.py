"""Programmatic API — the stable embedding surface for hallow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hallow.config import HallowConfig, load_config
from hallow.core.analyzer import analyze
from hallow.core.discovery import discover_python_files
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph
from hallow.types import AnalysisResults


def detect_dead_code(
    root: Path | str | None = None,
    config: HallowConfig | None = None,
) -> AnalysisResults:
    cfg = config or load_config(root=Path(root) if root else None)
    return analyze(cfg)


def detect_circular_dependencies(
    root: Path | str | None = None,
    config: HallowConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_config(root=Path(root) if root else None)
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)
    modules = extract_modules_parallel(files, root_path)
    graph = ModuleGraph(modules, root_path)

    cycles = graph.find_cycles()
    return [
        {
            "modules": c.modules,
            "edges": [list(e) for e in c.edges],
        }
        for c in cycles
    ]


def compute_complexity(
    root: Path | str | None = None,
    config: HallowConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_config(root=Path(root) if root else None)
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)
    modules = extract_modules_parallel(files, root_path)

    results = []
    for path, module in sorted(modules.items()):
        for func in module.functions:
            results.append(
                {
                    "file": path,
                    "name": func.name,
                    "kind": func.kind,
                    "line": func.line,
                    "cyclomatic": func.cyclomatic,
                    "cognitive": func.cognitive,
                    "parameters": func.parameters,
                    "lines_of_code": func.lines_of_code,
                }
            )

    return results
