"""Tests for dead-code detectors (import usage analysis)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.detectors import (
    _star_exported_names,
    detect_unlisted_dependencies,
    detect_unused_exports,
    detect_unused_imports,
)
from hallow.extract import extract_modules_parallel
from hallow.graph import ModuleGraph
from hallow.types import RuleId


def _build_graph(root: Path, files: dict[str, str]):
    for name, src in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src))
    paths = sorted(root.rglob("*.py"))
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


# ── unused-exports (star imports) ──


def test_init_star_import_with_all_not_flagged():
    # `from .api import *` in a package __init__ re-exports everything in
    # api.__all__: public API, like the names an __init__ re-exports one by one.
    msgs = _unused_exports(
        {
            "pkg/__init__.py": "from .api import *\n",
            "pkg/api.py": """
                __all__ = ["get", "Client"]

                def get():
                    return 1

                class Client:
                    pass
            """,
        }
    )
    assert msgs == [], msgs


def test_init_star_import_without_all_not_flagged():
    # Without __all__ a star import takes every public module-level name.
    msgs = _unused_exports(
        {
            "pkg/__init__.py": "from .models import *\n",
            "pkg/models.py": """
                LIMIT = 10

                class Model:
                    pass

                def build():
                    return 1
            """,
        }
    )
    assert msgs == [], msgs


def test_star_imported_name_nobody_uses_is_flagged():
    # Outside a package __init__ a star import is not a re-export: it consumes
    # the names the importing module references, and no others.
    msgs = _unused_exports(
        {
            "helpers.py": """
                def used_helper():
                    return 1

                def unused_helper():
                    return 2
            """,
            "app.py": """
                from helpers import *

                def run():
                    return used_helper()
            """,
        }
    )
    assert msgs == ["'unused_helper' is defined but never used or imported"], msgs


def test_star_imported_name_passed_on_by_importer_not_flagged():
    # The importing module can pass a star-imported name on without
    # referencing it: listed in its own __all__, imported from it by name, or
    # read from it as an attribute. Only that name is consumed.
    helpers = """
        def shared():
            return 1

        def unshared():
            return 2
    """
    for files in (
        {"facade.py": 'from helpers import *\n\n__all__ = ["shared"]\n'},
        {"facade.py": "from helpers import *\n", "app.py": "from facade import shared\n"},
        {"facade.py": "from helpers import *\n", "app.py": "import facade\n\nfacade.shared()\n"},
    ):
        msgs = _unused_exports({"helpers.py": helpers, **files})
        assert msgs == ["'unshared' is defined but never used or imported"], files


def test_star_import_chain_not_flagged():
    # `middle` star-imports `base` and is itself star-imported, so base's names
    # travel on to `app`. The chain is not followed name by name: everything
    # `middle` star-imports counts as used, including what `app` never touches.
    msgs = _unused_exports(
        {
            "base.py": """
                def used_at_the_end():
                    return 1

                def used_nowhere():
                    return 2
            """,
            "middle.py": "from base import *\n",
            "app.py": """
                from middle import *

                def run():
                    return used_at_the_end()
            """,
        }
    )
    assert msgs == [], msgs


def test_nested_package_star_import_not_flagged():
    # pkg/__init__ star-imports the subpackage, whose __init__ star-imports the
    # module that defines the names.
    msgs = _unused_exports(
        {
            "pkg/__init__.py": "from .sub import *\n",
            "pkg/sub/__init__.py": "from .impl import *\n",
            "pkg/sub/impl.py": """
                __all__ = ["Transport"]

                class Transport:
                    pass
            """,
        }
    )
    assert msgs == [], msgs


def test_module_nobody_star_imports_is_flagged_as_before():
    # A star import consumes names of the module it names and of no other:
    # `util` is only imported by name, so its unused function is still reported.
    with TemporaryDirectory() as tmpdir:
        graph, cfg = _build_graph(
            Path(tmpdir),
            {
                "pkg/__init__.py": "from .api import *\nfrom .util import used_thing\n",
                "pkg/api.py": "def call():\n    return 1\n",
                "pkg/util.py": """
                    def used_thing():
                        return 1

                    def orphan_thing():
                        return 2
                """,
            },
        )
        [f] = [f for f in detect_unused_exports(graph, cfg) if f.location.file == "pkg/util.py"]
    assert (f.rule, f.symbol, f.location.line) == (RuleId.UNUSED_FUNCTIONS, "orphan_thing", 5)
    assert f.message == "'orphan_thing' is defined but never used or imported"


def test_annotation_without_value_is_not_star_exported():
    # `limit: int` binds nothing, so `from settings import *` cannot provide it
    # and the importer's own `limit` is not a use of it.
    msgs = _unused_exports(
        {
            "settings.py": "limit: int\n",
            "app.py": "from settings import *\n\nlimit = 5\nprint(limit)\n",
        }
    )
    assert msgs == ["'limit' is defined but never used or imported"], msgs


def test_star_exported_names_follow_all_unless_modified():
    # A literal __all__ decides what a star import takes. Without one, or when
    # __all__ is changed after the literal list, every public name counts.
    body = "listed = public = _private = 1\n"
    with TemporaryDirectory() as tmpdir:
        graph, _ = _build_graph(
            Path(tmpdir),
            {
                "literal.py": '__all__ = ["listed"]\n' + body,
                "modified.py": '__all__ = ["listed"]\n__all__ += ["public"]\n' + body,
                "absent.py": body,
            },
        )
        names = {path: _star_exported_names(module) for path, module in graph.modules.items()}
    assert names == {
        "literal.py": {"listed"},
        "modified.py": {"listed", "public"},
        "absent.py": {"listed", "public"},
    }


# ── unlisted-dependencies (import->package alias resolution) ──


def _unlisted_deps(files: dict[str, str], pyproject: str) -> list[str]:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text(textwrap.dedent(pyproject))
        for name, src in files.items():
            (root / name).write_text(textwrap.dedent(src))
        paths = sorted(root.glob("*.py"))
        modules = extract_modules_parallel(paths, root)
        graph = ModuleGraph(modules, root)
        cfg = HallowConfig(root=root)
        return [f.message for f in detect_unlisted_dependencies(graph, cfg)]


def test_aliased_dependency_not_flagged_as_unlisted():
    # `import jwt` is satisfied by the declared PyJWT distribution.
    msgs = _unlisted_deps(
        {"mod.py": "import jwt as pyjwt\n\nx = pyjwt\n"},
        pyproject="""
            [project]
            name = "demo"
            dependencies = ["pyjwt>=2.8.0"]
        """,
    )
    assert not any("jwt" in m for m in msgs), msgs


def test_truly_unlisted_dependency_is_flagged():
    msgs = _unlisted_deps(
        {"mod.py": "import requests\n\nx = requests\n"},
        pyproject="""
            [project]
            name = "demo"
            dependencies = ["pyjwt>=2.8.0"]
        """,
    )
    assert any("requests" in m for m in msgs), msgs
