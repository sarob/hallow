"""Programmatic API — the stable embedding surface for hallow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hallow.config import HallowConfig, load_config
from hallow.core.analyzer import analyze
from hallow.core.discovery import discover_python_files
from hallow.core.duplicates import detect_duplicates
from hallow.core.health import compute_project_health
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph
from hallow.types import AnalysisResults, ProjectHealth


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


def compute_health(
    root: Path | str | None = None,
    config: HallowConfig | None = None,
) -> ProjectHealth:
    cfg = config or load_config(root=Path(root) if root else None)
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)
    modules = extract_modules_parallel(files, root_path)
    return compute_project_health(modules, cfg)


def find_duplicates(
    root: Path | str | None = None,
    config: HallowConfig | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_config(root=Path(root) if root else None)
    if mode:
        cfg.duplicates.mode = mode
    root_path = cfg.root.resolve()
    files = discover_python_files(cfg)
    groups, _ = detect_duplicates(files, root_path, cfg)
    return [g.model_dump(mode="json") for g in groups]
