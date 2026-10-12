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


def test_namespace_package_import_is_internal():
    # PEP 420: `ns/` has no __init__.py, but `import ns.mod` / `from ns import mod`
    # are project imports, not third-party dependencies.
    modules = {
        "app.py": _make_module("app.py", [_import("ns", ["mod"]), _import("ns.sub")]),
        "ns/mod.py": _make_module("ns/mod.py"),
        "ns/sub/thing.py": _make_module("ns/sub/thing.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    assert graph.all_external_imports() == set()
    assert "ns/mod.py" in graph.imports_of("app.py")


def test_namespace_child_missing_locally_is_external():
    # Review of #18: ns/ holds local code, but `ns.remote` comes from an
    # installed distribution sharing the namespace.
    modules = {
        "app.py": _make_module("app.py", [_import("ns", ["remote"])]),
        "ns/local.py": _make_module("ns/local.py"),
    }
    assert ModuleGraph(modules, Path(".")).all_external_imports() == {"ns"}


def test_mixed_namespace_import_keeps_local_edge_and_external_name():
    modules = {
        "app.py": _make_module("app.py", [_import("ns", ["local", "remote"])]),
        "ns/local.py": _make_module("ns/local.py"),
    }
    graph = ModuleGraph(modules, Path("."))
    assert graph.all_external_imports() == {"ns"}
    assert "ns/local.py" in graph.imports_of("app.py")


def test_src_layout_namespace_package_is_internal():
    modules = {
        "src/app.py": _make_module("src/app.py", [_import("ns", ["mod"]), _import("ns.sub")]),
        "src/ns/mod.py": _make_module("src/ns/mod.py"),
        "src/ns/sub/thing.py": _make_module("src/ns/sub/thing.py"),
    }
    assert ModuleGraph(modules, Path(".")).all_external_imports() == set()


def test_declared_namespace_distribution_is_not_reported_unused(tmp_path):
    # The detector-level consequence: losing the external name made a
    # declared, installed dependency look unused.
    from hallow.config.loader import HallowConfig
    from hallow.core import analyze

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["ns"]\n')
    (tmp_path / "ns").mkdir()
    (tmp_path / "ns" / "local.py").write_text("x = 1\n")
    (tmp_path / "app.py").write_text("from ns import local, remote\nprint(local, remote)\n")
    rules = {f.rule.value for f in analyze(HallowConfig(root=tmp_path)).findings}
    assert "unused-dependencies" not in rules
    assert "unlisted-dependencies" not in rules
