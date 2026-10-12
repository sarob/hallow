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


def test_bracket_classes_have_fnmatch_semantics():
    # Regression (review of #11): `[]]` crashed re.compile, and `[^a]` was read as
    # negation, though fnmatch treats `^` as a literal and only `!` negates.
    import fnmatch

    names = ["a.py", "b.py", "^.py", "].py", "!.py", "-.py", "c.py"]
    for pattern in ["[]].py", "[^a].py", "[!a].py", "[a-c].py", "[!]].py", "[-a].py", "[.py"]:
        for name in names:
            assert matches_glob(name, pattern) == fnmatch.fnmatchcase(name, pattern), (
                pattern,
                name,
            )


def test_bracket_pattern_in_config_does_not_abort_discovery():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "].py")
        _touch(root, "keep.py")
        assert _discovered(root, ignore_patterns=["[]].py"]) == ["keep.py"]


def test_nested_build_dist_venv_discovery():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "pkg/core.py")
        _touch(root, "pkg/build/lib/core.py")
        _touch(root, "pkg/dist/x.py")
        _touch(root, "pkg/venv/lib/site.py")
        assert _discovered(root) == ["pkg/core.py"]


def test_env_markers_must_have_the_right_type():
    # A source dir with a *file* named conda-meta, or a *directory* named
    # pyvenv.cfg, is not an environment.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "tool/a.py")
        (root / "tool" / "conda-meta").write_text("not a dir\n")
        _touch(root, "other/b.py")
        (root / "other" / "pyvenv.cfg").mkdir()
        assert _discovered(root) == ["other/b.py", "tool/a.py"]


def test_root_itself_is_scanned_even_if_it_is_an_environment():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
        _touch(root, "lib/x.py")
        assert _discovered(root) == ["lib/x.py"]


def test_ignored_directories_are_pruned_not_walked(monkeypatch):
    import hallow.core.discovery as discovery

    seen: list[str] = []
    real = discovery._is_env_dir

    def spy(path: Path) -> bool:
        seen.append(path.name)
        return real(path)

    monkeypatch.setattr(discovery, "_is_env_dir", spy)
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "app/main.py")
        _touch(root, "build/lib/deep/x.py")
        _discovered(root)
    # `build` is rejected by the ignore pattern before any env check, and
    # nothing beneath it is ever visited.
    assert "app" in seen
    assert not {"build", "lib", "deep"} & set(seen)


def test_slashless_pattern_naming_a_directory_prunes_it():
    # Documented behaviour change: a pattern without `/` also matches directory
    # names, so `generated` excludes the whole `generated/` tree.
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _touch(root, "generated/app.py")
        _touch(root, "main.py")
        assert _discovered(root, ignore_patterns=["generated"]) == ["main.py"]
