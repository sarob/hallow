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


# ── review of #16 ──


def test_reassigned_or_mutated_all_is_skipped():
    for src in (
        '__all__ = ["gone"]\n__all__ = []\n',
        '__all__ = ["gone"]\n__all__.remove("gone")\n',
        '__all__ = ["gone"]\n__all__.clear()\n',
        # review of #16, round 2: each of these left a false "gone" warning
        '__all__ = ["gone"]\n__all__ *= 0\n',
        '__all__ = ["gone"]\ndel __all__[:]\n',
        '__all__ = ["gone"]\n__all__[:] = []\n',
        '__all__ = ["gone"]\n(__all__ := [])\n',
        '__all__ = ["gone"]\ndef reset():\n    global __all__\n    __all__ = []\n',
        # review of #21, round 2: unpacking rebinds __all__ too
        '__all__ = ["gone"]\n__all__, marker = [], 0\n',
        '__all__ = ["gone"]\n[__all__] = [[]]\n',
        '__all__ = ["gone"]\nfor __all__ in ([],):\n    pass\n',
    ):
        assert _entries({"m.py": src}) == [], src


def test_local_or_read_only_use_of_all_does_not_hide_findings():
    # Review of #16, round 2: these do not change the module's __all__.
    expected = ["'gone' is listed in __all__ but not defined or imported"]
    local = '__all__ = ["gone"]\ndef f():\n    __all__ = []\n    return __all__\n'
    read = '__all__ = ["gone"]\nN = __all__.count("gone")\n'
    assert _entries({"m.py": local}) == expected
    assert _entries({"m.py": read}) == expected


def test_namespace_subpackage_and_excluded_submodule_count(tmp_path):
    # Through analyze(), so discovery applies: pkg/ns has no __init__.py
    # (PEP 420), and pkg/build is excluded from analysis by the default
    # `**/build/**` ignore but is still importable.
    from hallow.core import analyze
    from hallow.types import RuleId

    for rel, text in {
        "pkg/__init__.py": '__all__ = ["ns", "build", "missing"]\n',
        "pkg/ns/mod.py": "x = 1\n",
        "pkg/build/__init__.py": "y = 2\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    cfg = HallowConfig(root=tmp_path)
    # build/ really is excluded from analysis, yet `build` is not reported
    from hallow.core.discovery import discover_python_files

    found = {f.relative_to(tmp_path.resolve()).as_posix() for f in discover_python_files(cfg)}
    assert "pkg/build/__init__.py" not in found
    results = analyze(cfg)
    msgs = [f.message for f in results.findings if f.rule == RuleId.UNUSED_ALL_ENTRIES]
    assert msgs == ["'missing' is listed in __all__ but not defined or imported"]


def test_implicit_module_names_are_bound():
    assert _entries({"m.py": '__all__ = ["__name__", "__doc__"]\n'}) == []
    assert _entries({"pkg/__init__.py": '__all__ = ["__path__"]\n'}) == []


def test_annotation_without_value_binds_nothing():
    msgs = _entries({"m.py": 'x: int\n__all__ = ["x"]\n'})
    assert msgs == ["'x' is listed in __all__ but not defined or imported"]


def test_finding_fields_through_analyze(tmp_path):
    from hallow.core import analyze
    from hallow.types import RuleId, Severity

    (tmp_path / "m.py").write_text('import os\nprint(os)\n\n__all__ = ["gone"]\n')
    [f] = [
        f for f in analyze(HallowConfig(root=tmp_path)).findings
        if f.rule == RuleId.UNUSED_ALL_ENTRIES
    ]  # fmt: skip
    assert (f.severity, f.location.file, f.location.line) == (Severity.WARN, "m.py", 4)
    assert (f.fix.kind, f.fix.target, f.fix.auto_fixable) == ("remove_from_all", "gone", False)

    off = HallowConfig(root=tmp_path)
    off.rules.overrides["unused-all-entries"] = Severity.OFF
    assert not [f for f in analyze(off).findings if f.rule == RuleId.UNUSED_ALL_ENTRIES]


def test_unpacked_rebinding_through_analyze(tmp_path):
    from hallow.core import analyze
    from hallow.types import RuleId

    (tmp_path / "m.py").write_text('__all__ = ["gone"]\n__all__, marker = [], 0\n')
    rules = {f.rule for f in analyze(HallowConfig(root=tmp_path)).findings}
    assert RuleId.UNUSED_ALL_ENTRIES not in rules


def test_annotation_only_redeclaration_is_not_a_rebinding():
    msgs = _entries({"m.py": '__all__ = ["gone"]\n__all__: list[str]\n'})
    assert msgs == ["'gone' is listed in __all__ but not defined or imported"]
