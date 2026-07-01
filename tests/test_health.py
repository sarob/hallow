"""Tests for health scoring."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile

from hallow.config.loader import HallowConfig
from hallow.core.health import compute_file_health, compute_project_health, detect_high_complexity
from hallow.extract import extract_module
from hallow.types import FunctionComplexity, ModuleInfo


def _module_with_functions(funcs: list[FunctionComplexity], loc: int = 50) -> ModuleInfo:
    return ModuleInfo(
        path="test.py",
        package="test",
        functions=funcs,
        line_count=loc,
    )


def test_file_health_no_functions():
    module = _module_with_functions([], loc=10)
    fh = compute_file_health("test.py", module, HallowConfig())
    assert fh.maintainability_index == 100.0
    assert fh.cyclomatic_avg == 0.0


def test_file_health_simple_function():
    funcs = [
        FunctionComplexity(name="simple", kind="function", line=1, cyclomatic=1, cognitive=0),
    ]
    module = _module_with_functions(funcs, loc=5)
    fh = compute_file_health("test.py", module, HallowConfig())
    assert fh.cyclomatic_avg == 1.0
    assert fh.cyclomatic_max == 1
    assert fh.maintainability_index > 0


def test_file_health_complex_function():
    funcs = [
        FunctionComplexity(name="complex", kind="function", line=1, cyclomatic=25, cognitive=20),
    ]
    module = _module_with_functions(funcs, loc=100)
    cfg = HallowConfig()
    fh = compute_file_health("test.py", module, cfg)
    assert fh.cyclomatic_max == 25
    assert "complex" in fh.hotspot_functions


def test_project_health_grade():
    funcs = [
        FunctionComplexity(name="f1", kind="function", line=1, cyclomatic=2, cognitive=1),
    ]
    modules = {
        "a.py": _module_with_functions(funcs, loc=20),
        "b.py": _module_with_functions(funcs, loc=15),
    }
    health = compute_project_health(modules, HallowConfig())
    assert health.grade in ("A", "B", "C", "D", "F")
    assert 0 <= health.score <= 100
    assert health.total_files == 2


def test_detect_high_complexity():
    funcs = [
        FunctionComplexity(name="big", kind="function", line=10, cyclomatic=25, cognitive=20),
        FunctionComplexity(name="small", kind="function", line=20, cyclomatic=2, cognitive=1),
    ]
    modules = {"test.py": _module_with_functions(funcs)}
    cfg = HallowConfig()
    findings = detect_high_complexity(modules, cfg)
    names = [f.message for f in findings]
    assert any("big" in m for m in names)
    assert not any("small" in m for m in names)


def test_health_from_real_source():
    source = textwrap.dedent("""
        def simple():
            return 1

        def branchy(x, y, z):
            if x > 0:
                for i in range(y):
                    if i % 2 == 0:
                        pass
            elif y < 0:
                while z:
                    z -= 1
            return x + y + z
    """)
    with NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        path = Path(f.name)

    info = extract_module(path, root=path.parent)
    path.unlink()
    assert info is not None

    fh = compute_file_health(info.path, info, HallowConfig())
    assert fh.cyclomatic_max > 1
    assert fh.maintainability_index > 0
