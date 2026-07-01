"""Tests for architecture boundary enforcement."""

from __future__ import annotations

from pathlib import Path

from hallow.config.loader import BoundaryConfig, BoundaryRule, BoundaryZone, HallowConfig
from hallow.core.boundaries import detect_boundary_violations, get_preset
from hallow.graph import ModuleGraph
from hallow.types import ImportInfo, ModuleInfo


def _make_module(path: str, imports: list[ImportInfo] | None = None) -> ModuleInfo:
    return ModuleInfo(
        path=path,
        package=path.replace("/", ".").replace(".py", ""),
        imports=imports or [],
        line_count=10,
    )


def _import(module: str, names: list[str] | None = None):
    return ImportInfo(
        module=module,
        names=names or [],
        is_from_import=bool(names),
        line=1,
    )


def test_presets_exist():
    assert get_preset("layered") is not None
    assert get_preset("hexagonal") is not None
    assert get_preset("feature-sliced") is not None
    assert get_preset("nonexistent") is None


def test_no_violations_within_same_zone():
    modules = {
        "services/auth.py": _make_module(
            "services/auth.py", [_import("services.users", ["get_user"])]
        ),
        "services/users.py": _make_module("services/users.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    cfg = HallowConfig(
        boundaries=BoundaryConfig(
            zones=[BoundaryZone(name="business", patterns=["**/services/**"])],
            rules=[BoundaryRule(**{"from": "business", "allow": ["data"]})],
        )
    )
    findings = detect_boundary_violations(graph, cfg)
    assert len(findings) == 0


def test_detects_violation():
    modules = {
        "data/repo.py": _make_module("data/repo.py", [_import("views.home", ["render"])]),
        "views/home.py": _make_module("views/home.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    cfg = HallowConfig(
        boundaries=BoundaryConfig(
            zones=[
                BoundaryZone(name="data", patterns=["**/data/**"]),
                BoundaryZone(name="presentation", patterns=["**/views/**"]),
            ],
            rules=[BoundaryRule(**{"from": "data", "allow": []})],
        )
    )
    findings = detect_boundary_violations(graph, cfg)
    assert len(findings) == 1
    assert "data" in findings[0].message
    assert "presentation" in findings[0].message


def test_allowed_import_no_violation():
    modules = {
        "views/home.py": _make_module("views/home.py", [_import("services.auth", ["check"])]),
        "services/auth.py": _make_module("services/auth.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    cfg = HallowConfig(
        boundaries=BoundaryConfig(
            zones=[
                BoundaryZone(name="presentation", patterns=["**/views/**"]),
                BoundaryZone(name="business", patterns=["**/services/**"]),
            ],
            rules=[BoundaryRule(**{"from": "presentation", "allow": ["business"]})],
        )
    )
    findings = detect_boundary_violations(graph, cfg)
    assert len(findings) == 0


def test_no_config_no_findings():
    modules = {
        "a.py": _make_module("a.py", [_import("b", ["foo"])]),
        "b.py": _make_module("b.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    cfg = HallowConfig()
    findings = detect_boundary_violations(graph, cfg)
    assert len(findings) == 0
