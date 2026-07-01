"""Tests for AST extraction."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile

from hallow.extract import extract_module


def _extract_source(source: str) -> dict:
    source = textwrap.dedent(source)
    with NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        path = Path(f.name)
    info = extract_module(path, root=path.parent)
    path.unlink()
    assert info is not None
    return info


def test_extracts_imports():
    info = _extract_source("""
        import os
        from pathlib import Path
        from typing import Any
    """)
    assert len(info.imports) == 3
    assert info.imports[0].module == "os"
    assert info.imports[1].module == "pathlib"
    assert info.imports[1].names == ["Path"]


def test_extracts_functions():
    info = _extract_source("""
        def hello():
            pass

        def greet(name: str) -> str:
            return f"hello {name}"
    """)
    exports = [e.name for e in info.exports]
    assert "hello" in exports
    assert "greet" in exports


def test_extracts_classes():
    info = _extract_source("""
        class Foo:
            pass

        class Bar(Foo):
            def method(self):
                pass
    """)
    assert "Foo" in info.classes
    assert "Bar" in info.classes


def test_extracts_all_list():
    info = _extract_source("""
        __all__ = ["foo", "bar"]

        def foo(): pass
        def bar(): pass
        def _private(): pass
    """)
    assert info.all_list == ["foo", "bar"]


def test_cyclomatic_complexity():
    info = _extract_source("""
        def simple():
            return 1

        def complex_fn(x):
            if x > 0:
                for i in range(x):
                    if i % 2 == 0:
                        pass
            elif x < 0:
                while True:
                    break
            return x
    """)
    funcs = {f.name: f for f in info.functions}
    assert funcs["simple"].cyclomatic == 1
    assert funcs["complex_fn"].cyclomatic > 1


def test_relative_imports():
    info = _extract_source("""
        from . import sibling
        from ..parent import something
    """)
    assert info.imports[0].is_relative
    assert info.imports[0].level == 1
    assert info.imports[1].is_relative
    assert info.imports[1].level == 2


def test_type_checking_imports():
    info = _extract_source("""
        from __future__ import annotations
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from pathlib import Path
    """)
    type_checking_imports = [i for i in info.imports if i.is_type_checking]
    assert len(type_checking_imports) == 1
    assert type_checking_imports[0].module == "pathlib"


def test_decorators():
    info = _extract_source("""
        import functools

        @functools.lru_cache
        def cached():
            pass

        @staticmethod
        def static_method():
            pass
    """)
    exports = {e.name: e for e in info.exports}
    assert "lru_cache" in exports["cached"].decorators
    assert "staticmethod" in exports["static_method"].decorators


def test_syntax_error_returns_none():
    with NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def broken(:\n")
        f.flush()
        path = Path(f.name)
    info = extract_module(path, root=path.parent)
    path.unlink()
    assert info is None
