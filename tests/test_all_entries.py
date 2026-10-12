"""Tests for the unused-all-entries rule."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.detectors import detect_unused_all_entries
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph


def _entries(files: dict[str, str]) -> list[str]:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, src in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(src))
        modules = extract_modules_parallel(sorted(root.rglob("*.py")), root)
        graph = ModuleGraph(modules, root)
        return [f.message for f in detect_unused_all_entries(graph, HallowConfig(root=root))]


def test_flags_entry_naming_nothing():
    msgs = _entries({"m.py": '__all__ = ["present", "renamed_away"]\ndef present(): ...\n'})
    assert msgs == ["'renamed_away' is listed in __all__ but not defined or imported"]


def test_bindings_in_branches_imports_and_unpacking_count():
    msgs = _entries(
        {
            "m.py": """\
            import os as operating
            from json import loads
            a, (b, c) = 1, (2, 3)
            try:
                import tomllib as toml
            except ImportError:
                toml = None
            if True:
                def cond(): ...
            def setter():
                global late
                late = 1
            __all__ = ["operating", "loads", "a", "b", "c", "toml", "cond", "late"]
        """,
        }
    )
    assert msgs == []


def test_package_init_may_list_submodules():
    msgs = _entries(
        {
            "pkg/__init__.py": '__all__ = ["sub", "missing"]\n',
            "pkg/sub.py": "x = 1\n",
        }
    )
    assert msgs == ["'missing' is listed in __all__ but not defined or imported"]


def test_dynamic_modules_skipped():
    assert _entries({"m.py": 'from os.path import *\n__all__ = ["join"]\n'}) == []
    assert _entries({"m.py": 'globals()["x"] = 1\n__all__ = ["x"]\n'}) == []
    assert _entries({"m.py": 'def __getattr__(name): ...\n__all__ = ["lazy"]\n'}) == []
