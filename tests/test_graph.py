"""Tests for module graph construction and cycle detection."""

from __future__ import annotations

from pathlib import Path

from hallow.graph import ModuleGraph
from hallow.types import ImportInfo, ModuleInfo


def _make_module(path: str, imports: list[ImportInfo] | None = None) -> ModuleInfo:
    is_init = path.endswith("__init__.py")
    is_test = "test_" in path
    return ModuleInfo(
        path=path,
        package=path.replace("/", ".").replace(".py", ""),
        imports=imports or [],
        is_init=is_init,
        is_test=is_test,
        line_count=10,
    )


def _import(module: str, names: list[str] | None = None, relative: bool = False, level: int = 0):
    return ImportInfo(
        module=module,
        names=names or [],
        is_from_import=bool(names),
        is_relative=relative,
        level=level,
        line=1,
    )


def test_basic_graph():
    modules = {
        "a.py": _make_module("a.py", [_import("b", ["foo"])]),
        "b.py": _make_module("b.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    assert "b.py" in graph.imports_of("a.py")
    assert "a.py" in graph.importers_of("b.py")


def test_cycle_detection():
    modules = {
        "a.py": _make_module("a.py", [_import("b", ["something"])]),
        "b.py": _make_module("b.py", [_import("a", ["something"])]),
    }
    graph = ModuleGraph(modules, Path("."))
    cycles = graph.find_cycles()
    assert len(cycles) == 1
    assert set(cycles[0].modules) == {"a.py", "b.py"}


def test_no_cycles():
    modules = {
        "a.py": _make_module("a.py", [_import("b", ["foo"])]),
        "b.py": _make_module("b.py", [_import("c", ["bar"])]),
        "c.py": _make_module("c.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    cycles = graph.find_cycles()
    assert len(cycles) == 0


def test_external_imports():
    modules = {
        "app.py": _make_module(
            "app.py",
            [
                _import("flask", ["Flask"]),
                _import("os"),
            ],
        ),
    }
    graph = ModuleGraph(modules, Path("."))
    ext = graph.all_external_imports()
    assert "flask" in ext
    assert "os" in ext


def test_absolute_from_package_import_submodule_edge():
    # `from pkg import submodule` must create an edge to pkg/submodule.py.
    modules = {
        "pkg/__init__.py": _make_module("pkg/__init__.py"),
        "pkg/sub.py": _make_module("pkg/sub.py"),
        "app.py": _make_module("app.py", [_import("pkg", ["sub"])]),
    }
    graph = ModuleGraph(modules, Path("."))
    assert "pkg/sub.py" in graph.imports_of("app.py")
    assert "app.py" in graph.importers_of("pkg/sub.py")


def test_relative_from_package_import_submodule_edge():
    modules = {
        "pkg/__init__.py": _make_module("pkg/__init__.py"),
        "pkg/sub.py": _make_module("pkg/sub.py"),
        "pkg/app.py": _make_module(
            "pkg/app.py",
            [ImportInfo(module="", names=["sub"], is_from_import=True, is_relative=True, level=1)],
        ),
    }
    graph = ModuleGraph(modules, Path("."))
    assert "pkg/sub.py" in graph.imports_of("pkg/app.py")


def test_symbol_import_does_not_create_spurious_submodule_edge():
    # `from pkg import helper` where helper is a symbol (no pkg/helper.py) must
    # not invent an edge.
    modules = {
        "pkg/__init__.py": _make_module("pkg/__init__.py"),
        "app.py": _make_module("app.py", [_import("pkg", ["helper"])]),
    }
    graph = ModuleGraph(modules, Path("."))
    assert graph.imports_of("app.py") == {"pkg/__init__.py"}


def test_unreachable_files():
    modules = {
        "main.py": _make_module("main.py", [_import("lib", ["helper"])]),
        "lib.py": _make_module("lib.py"),
        "orphan.py": _make_module("orphan.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    unreachable = graph.unreachable_files()
    assert "lib.py" not in unreachable
