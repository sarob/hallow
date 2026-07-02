"""Tests for dead-code detectors (import usage analysis)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.detectors import detect_unused_imports
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph


def _unused_imports(files: dict[str, str]) -> list[str]:
    """Write files to a temp project, run unused-import detection, return messages."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        for name, src in files.items():
            (root / name).write_text(textwrap.dedent(src))
        paths = sorted(root.glob("*.py"))
        modules = extract_modules_parallel(paths, root)
        graph = ModuleGraph(modules, root)
        cfg = HallowConfig(root=root)
        return [f.message for f in detect_unused_imports(graph, cfg)]


def test_dotted_import_used_is_not_flagged():
    # Regression: `import a.b` binds `a`; usage `a.b.x` must count as used.
    msgs = _unused_imports(
        {
            "mod.py": """
                import email.utils

                def parse(raw):
                    return email.utils.parsedate_to_datetime(raw)
            """
        }
    )
    assert msgs == [], msgs


def test_dotted_import_unused_is_flagged_by_bound_name():
    msgs = _unused_imports(
        {
            "mod.py": """
                import email.utils

                def noop():
                    return 1
            """
        }
    )
    assert len(msgs) == 1
    # Reported by the actual bound name (`email`), not the leaf (`utils`).
    assert "email" in msgs[0]
    assert "import email.utils" in msgs[0]


def test_from_import_unused_is_flagged():
    # Regression: genuinely-unused from-imports were never caught (false negative).
    msgs = _unused_imports(
        {
            "mod.py": """
                from os import getcwd

                def noop():
                    return 1
            """
        }
    )
    assert len(msgs) == 1
    assert "getcwd" in msgs[0]


def test_from_import_used_is_not_flagged():
    msgs = _unused_imports(
        {
            "mod.py": """
                from os import getcwd

                def where():
                    return getcwd()
            """
        }
    )
    assert msgs == [], msgs


def test_aliased_from_import_used_is_not_flagged():
    # `from os import getcwd as gc` binds `gc`; checking the source name would misfire.
    msgs = _unused_imports(
        {
            "mod.py": """
                from os import getcwd as gc

                def where():
                    return gc()
            """
        }
    )
    assert msgs == [], msgs


def test_plain_import_used_is_not_flagged():
    msgs = _unused_imports(
        {
            "mod.py": """
                import os

                def where():
                    return os.getcwd()
            """
        }
    )
    assert msgs == [], msgs


def test_future_import_is_never_flagged():
    # `from __future__ import annotations` is a compiler directive, never a name.
    msgs = _unused_imports(
        {
            "mod.py": """
                from __future__ import annotations

                def noop():
                    return 1
            """
        }
    )
    assert msgs == [], msgs


def test_noqa_f401_suppresses_unused_import():
    # Side-effect / back-compat imports marked `# noqa: F401` must not be flagged.
    msgs = _unused_imports(
        {
            "mod.py": """
                from . import submodule  # noqa: F401  (registration side effects)

                def noop():
                    return 1
            """,
            "submodule.py": "x = 1\n",
        }
    )
    assert msgs == [], msgs


def test_bare_noqa_suppresses_unused_import():
    msgs = _unused_imports(
        {
            "mod.py": """
                import os  # noqa

                def noop():
                    return 1
            """
        }
    )
    assert msgs == [], msgs


def test_reexported_import_is_not_flagged():
    # An import re-exported via __all__ counts as used even without a Load.
    msgs = _unused_imports(
        {
            "mod.py": """
                from os import getcwd

                __all__ = ["getcwd"]
            """
        }
    )
    assert msgs == [], msgs
