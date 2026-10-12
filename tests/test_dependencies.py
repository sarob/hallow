"""Tests for declared-dependency reading and the dependency rules."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core import analyze
from hallow.core.dependencies import read_declared_dependencies, requirement_name
from hallow.core.fixer import apply_fixes


def _write(root: Path, files: dict[str, str]) -> None:
    for name, src in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))


def test_requirement_name_normalizes():
    assert requirement_name("Django>=4.2 ; python_version>'3.10'") == "django"
    assert requirement_name("python-dateutil[extra]==2.9") == "python_dateutil"
    assert requirement_name("zope.interface") == "zope_interface"


def test_pyproject_runtime_optional_and_groups_with_lines():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            {
                "pyproject.toml": """\
            [project]
            name = "x"
            dependencies = [
                "requests>=2",
            ]
            [project.optional-dependencies]
            cli = ["typer"]
            [dependency-groups]
            dev = ["pytest", {include-group = "lint"}]
        """
            },
        )
        got = [(d.name, d.file, d.line, d.optional) for d in read_declared_dependencies(root)]
        assert got == [
            ("requests", "pyproject.toml", 4, False),
            ("typer", "pyproject.toml", 7, True),
            ("pytest", "pyproject.toml", 9, True),
        ]


def test_requirements_fallback_without_project_table():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            {
                "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
                "requirements.txt": """\
                # runtime
                flask==3.0  # web
                -r requirements-extra.txt
                -e ./local_pkg
                https://example.com/pkg.tar.gz
                PyYAML>=6
            """,
                "requirements-extra.txt": "redis\n",
                "requirements-dev.txt": "pytest\n",
            },
        )
        got = [(d.name, d.file, d.line, d.optional) for d in read_declared_dependencies(root)]
        assert got == [
            ("flask", "requirements.txt", 2, False),
            ("redis", "requirements-extra.txt", 1, False),
            ("pyyaml", "requirements.txt", 6, False),
            ("pytest", "requirements-dev.txt", 1, True),
        ]


def test_rules_and_fix_use_requirements_txt():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            {
                "requirements.txt": "flask\nrequests\n",
                "app.py": "import flask\nimport httpx\nflask.Flask(httpx)\n",
            },
        )
        results = analyze(HallowConfig(root=root))
        got = {(f.rule.value, f.location.file, f.location.line) for f in results.findings}
        assert ("unused-dependencies", "requirements.txt", 2) in got
        # unlisted dependencies point at the import that needs them
        assert ("unlisted-dependencies", "app.py", 2) in got

        apply_fixes(results, root, dry_run=False)
        assert (root / "requirements.txt").read_text() == "flask\n"
