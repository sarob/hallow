"""Tests for file discovery and ignore-pattern matching."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.discovery import discover_python_files, matches_glob


def _touch(root: Path, rel: str, text: str = "x = 1\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _discovered(root: Path, **cfg) -> list[str]:
    files = discover_python_files(HallowConfig(root=root, **cfg))
    return sorted(f.relative_to(root.resolve()).as_posix() for f in files)


def test_double_star_matches_top_level_directory():
    # Regression: fnmatch needed a leading segment, so `**/build/**` missed `build/`.
    assert matches_glob("build/lib/app.py", "**/build/**")
    assert matches_glob("pkg/build/x.py", "**/build/**")
    assert matches_glob("dist/", "**/dist/**")
    assert not matches_glob("builder/x.py", "**/build/**")


def test_single_star_stays_in_one_segment():
    assert matches_glob("migrations/0001.py", "migrations/*.py")
    assert not matches_glob("migrations/sub/0001.py", "migrations/*.py")


def test_basename_pattern_matches_at_any_depth():
    assert matches_glob("a/b/conftest.py", "conftest.py")
    assert matches_glob("a/b/gen_pb2.py", "*_pb2.py")


def test_default_ignores_skip_top_level_build_dist_venv():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "app/main.py")
        _touch(root, "build/lib/app/main.py")
        _touch(root, "dist/pkg/x.py")
        _touch(root, "venv/lib/site.py")
        assert _discovered(root) == ["app/main.py"]


def test_virtualenv_detected_by_marker_not_name():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "src/app.py")
        _touch(root, "env-py312/lib/python3.12/site-packages/requests/__init__.py")
        (root / "env-py312" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        _touch(root, "conda/lib/thing.py")
        (root / "conda" / "conda-meta").mkdir()
        assert _discovered(root) == ["src/app.py"]


def test_hidden_directories_skipped():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "app.py")
        _touch(root, ".venv-py312/lib/x.py")
        _touch(root, ".tox/y.py")
        assert _discovered(root) == ["app.py"]


def test_user_ignore_pattern_prunes_directory():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "app.py")
        _touch(root, "app/migrations/0001_initial.py")
        found = _discovered(root, ignore_patterns=["**/migrations/**"])
        assert found == ["app.py"]
