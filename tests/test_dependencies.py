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


# ── review of #17 ──


def test_continued_requirement_is_one_entry_and_fix_removes_all_its_lines(tmp_path):
    # B1: `pkg==1 \` + indented `--hash=...` is one requirement.
    _write(
        tmp_path,
        {
            "requirements.txt": (
                "flask==3.0 \\\n    --hash=sha256:abc \\\n    --hash=sha256:def\n"
                "requests==2.0 \\\n    --hash=sha256:123\n"
            ),
            "app.py": "import flask\nflask.Flask(__name__)\n",
        },
    )
    got = [(d.name, d.line) for d in read_declared_dependencies(tmp_path)]
    assert got == [("flask", 1), ("requests", 4)]
    results = analyze(HallowConfig(root=tmp_path))
    apply_fixes(results, tmp_path, dry_run=False)
    assert (tmp_path / "requirements.txt").read_text() == (
        "flask==3.0 \\\n    --hash=sha256:abc \\\n    --hash=sha256:def\n"
    )


def test_local_paths_and_archives_are_not_dependencies(tmp_path):
    # B2
    _write(
        tmp_path,
        {
            "requirements.txt": (
                "vendor/local_pkg\n"
                "./other\n"
                "dist/thing-1.0-py3-none-any.whl\n"
                "thing-1.0.tar.gz\n"
                "C:\\\\pkgs\\\\x\n"
                "https://example.com/p.zip\n"
                "named @ https://example.com/named.zip\n"
                "real==1\n"
            )
        },
    )
    names = [d.name for d in read_declared_dependencies(tmp_path)]
    assert names == ["named", "real"]


def test_all_include_forms_are_followed(tmp_path):
    # B3
    _write(
        tmp_path,
        {
            "requirements.txt": (
                "-rreq/a.txt\n--requirement=req/b.txt\n--requirement req/c.txt\n-r req/d.txt\n"
            ),
            "req/a.txt": "alpha\n",
            "req/b.txt": "beta\n",
            "req/c.txt": "gamma\n",
            "req/d.txt": "delta\n",
        },
    )
    names = sorted(d.name for d in read_declared_dependencies(tmp_path))
    assert names == ["alpha", "beta", "delta", "gamma"]


def test_unlisted_detection_sees_included_files(tmp_path):
    _write(
        tmp_path,
        {
            "requirements.txt": "-rreq/base.txt\n",
            "req/base.txt": "flask\n",
            "app.py": "import flask\nflask.Flask(__name__)\n",
        },
    )
    rules = {f.rule.value for f in analyze(HallowConfig(root=tmp_path)).findings}
    assert "unlisted-dependencies" not in rules


def test_pyproject_location_ignores_comments_and_other_tables(tmp_path):
    # B4
    _write(
        tmp_path,
        {
            "pyproject.toml": """\
        # example: "requests>=2"
        [tool.other]
        x = ["requests>=2"]
        [project]
        name = "x"
        dependencies = [
            "requests>=2",
        ]
        [project.optional-dependencies]
        a = ["requests>=2"]
    """
        },
    )
    got = [(d.name, d.line, d.optional) for d in read_declared_dependencies(tmp_path)]
    assert got == [("requests", 7, False), ("requests", 10, True)]


def test_pyproject_fix_agrees_with_the_detector_on_names(tmp_path):
    # B5, and the trailing-comma bug on main (`"leftpad",` read as `leftpad"`)
    _write(
        tmp_path,
        {
            "pyproject.toml": """\
            [project]
            name = "x"
            dependencies = [
                "foo--bar>=1",
                "requests~=2",
                "leftpad",
                "kept",
            ]
        """,
            "app.py": "import kept\nkept.go()\n",
        },
    )
    results = analyze(HallowConfig(root=tmp_path))
    unused = sorted(f.fix.target for f in results.findings if f.rule.value == "unused-dependencies")
    assert unused == ["foo_bar", "leftpad", "requests"]
    fixed = apply_fixes(results, tmp_path, dry_run=False)
    assert sorted(fixed.removed_deps) == ["foo_bar", "leftpad", "requests"]
    text = (tmp_path / "pyproject.toml").read_text()
    assert '"kept",' in text
    assert "foo--bar" not in text and "requests" not in text and "leftpad" not in text


def test_plugins_activate_from_requirements_txt(tmp_path):
    from hallow.plugins.registry import load_plugins

    _write(tmp_path, {"requirements.txt": "fastapi==0.110\n"})
    assert [type(p).__name__ for p in load_plugins(tmp_path).plugins] == ["FastAPIPlugin"]
