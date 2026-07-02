"""Tests for dead-code detectors (import usage analysis)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.detectors import detect_unused_exports, detect_unused_imports
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph


def _build_graph(root: Path, files: dict[str, str]):
    for name, src in files.items():
        (root / name).write_text(textwrap.dedent(src))
    paths = sorted(root.glob("*.py"))
    modules = extract_modules_parallel(paths, root)
    return ModuleGraph(modules, root), HallowConfig(root=root)


def _unused_imports(files: dict[str, str]) -> list[str]:
    """Write files to a temp project, run unused-import detection, return messages."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph, cfg = _build_graph(root, files)
        return [f.message for f in detect_unused_imports(graph, cfg)]


def _unused_exports(files: dict[str, str]) -> list[str]:
    """Write files to a temp project, run unused-export detection, return messages."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph, cfg = _build_graph(root, files)
        return [f.message for f in detect_unused_exports(graph, cfg)]


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


# ── unused-exports (decorator entry points + intra-module usage) ──


def test_typer_command_handler_not_flagged():
    # `@app.command()` handlers are registered via decorator, never imported by
    # name. Regression for the Typer false-positive flood.
    msgs = _unused_exports(
        {
            "mod.py": """
                import typer

                app = typer.Typer()

                @app.command("list")
                def list_things():
                    return 1
            """,
            "entry.py": "from mod import app\n",
        }
    )
    assert not any("list_things" in m for m in msgs), msgs


def test_register_decorated_class_not_flagged():
    # `@register("name")` classes are loaded dynamically via a registry.
    msgs = _unused_exports(
        {
            "mod.py": """
                from registry import register

                @register("groq")
                class GroqRuntime:
                    pass
            """,
            "entry.py": "from mod import GroqRuntime  # noqa: F401\nimport mod\n",
        }
    )
    assert not any("GroqRuntime" in m for m in msgs), msgs


def test_intramodule_used_function_not_flagged():
    msgs = _unused_exports(
        {
            "mod.py": """
                def helper():
                    return 1

                def public():
                    return helper()
            """,
            "entry.py": "from mod import public\n",
        }
    )
    assert not any("helper" in m for m in msgs), msgs


def test_module_logger_not_flagged():
    # `logger = logging.getLogger(__name__)` used within the module.
    msgs = _unused_exports(
        {
            "mod.py": """
                import logging

                logger = logging.getLogger(__name__)

                def public():
                    logger.info("hi")
                    return 1
            """,
            "entry.py": "from mod import public\n",
        }
    )
    assert not any("logger" in m for m in msgs), msgs


def test_config_class_used_as_field_type_not_flagged():
    # Referenced only in an annotation within the same module.
    msgs = _unused_exports(
        {
            "mod.py": """
                class Inner:
                    pass

                class Outer:
                    child: Inner = None
            """,
            "entry.py": "from mod import Outer\n",
        }
    )
    assert not any("Inner" in m for m in msgs), msgs


def test_module_attribute_access_registration_not_flagged():
    # main.py imports the module and registers the handler via attribute access
    # (`app.command(...)(bootstrap.bootstrap_agent)`), not a named import.
    msgs = _unused_exports(
        {
            "bootstrap.py": """
                import typer

                def bootstrap_agent(name: str = typer.Argument(...)):
                    return name
            """,
            "main.py": """
                import typer
                from . import bootstrap

                app = typer.Typer()
                app.command("bootstrap-agent")(bootstrap.bootstrap_agent)
            """,
        }
    )
    assert not any("bootstrap_agent" in m for m in msgs), msgs


def test_genuinely_unused_export_is_flagged():
    # A public symbol neither consumed, referenced, nor decorated must still be
    # reported — the fix must not over-suppress.
    msgs = _unused_exports(
        {
            "mod.py": """
                def used_thing():
                    return 1

                def orphan_thing():
                    return 2
            """,
            "entry.py": "from mod import used_thing\n",
        }
    )
    assert any("orphan_thing" in m for m in msgs), msgs
    assert not any("used_thing" in m for m in msgs), msgs
