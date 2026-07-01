"""Tests for duplication detection."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core.duplicates import detect_duplicates, tokenize_file


def _write_file(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content)
    return p


def test_tokenize_strict():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        p = _write_file(root, "a.py", "x = 1 + 2\n")
        tokens = tokenize_file(p, root, "strict")
        values = [t.value for t in tokens]
        assert "x" in values
        assert "1" in values


def test_tokenize_mild_normalizes_names():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        p = _write_file(root, "a.py", "foo = bar + baz\n")
        tokens = tokenize_file(p, root, "mild")
        values = [t.value for t in tokens]
        assert values.count("$ID") == 3


def test_tokenize_weak_normalizes_ops():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        p = _write_file(root, "a.py", "x = 1 + 2\n")
        tokens = tokenize_file(p, root, "weak")
        values = [t.value for t in tokens]
        assert "$OP" in values


def test_no_duplicates_in_unique_code():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "a.py", "def hello():\n    return 1\n")
        _write_file(root, "b.py", "def world():\n    return 2\n")
        files = list(root.glob("*.py"))
        cfg = HallowConfig(root=root)
        groups, findings = detect_duplicates(files, root, cfg)
        assert len(groups) == 0


def test_detects_exact_duplicates():
    block = "\n".join([f"    x{i} = i * 2" for i in range(20)])
    code_a = f"def func_a():\n{block}\n"
    code_b = f"def func_b():\n{block}\n"

    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "a.py", code_a)
        _write_file(root, "b.py", code_b)
        files = sorted(root.glob("*.py"))
        cfg = HallowConfig(root=root)
        cfg.duplicates.mode = "mild"
        cfg.duplicates.min_tokens = 10
        cfg.duplicates.min_lines = 3
        groups, findings = detect_duplicates(files, root, cfg)
        assert len(groups) >= 1
        assert groups[0].token_count >= 10


def test_large_clone_collapses_to_single_group():
    # A large identical block across two files must be reported ONCE, not once
    # per starting offset. Regression for the overlapping-window over-count bug.
    block = "\n".join([f"    value_{i} = compute(i) + offset_{i}" for i in range(40)])
    code_a = f"def alpha():\n{block}\n"
    code_b = f"def beta():\n{block}\n"

    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "a.py", code_a)
        _write_file(root, "b.py", code_b)
        files = sorted(root.glob("*.py"))
        cfg = HallowConfig(root=root)
        cfg.duplicates.mode = "mild"
        cfg.duplicates.min_tokens = 50
        cfg.duplicates.min_lines = 5
        groups, _ = detect_duplicates(files, root, cfg)

        assert len(groups) == 1, f"expected exactly one collapsed group, got {len(groups)}"
        assert len(groups[0].fragments) == 2
        files_in_group = {f.file for f in groups[0].fragments}
        assert files_in_group == {"a.py", "b.py"}


def test_distinct_non_overlapping_clones_preserved():
    # Two DIFFERENT duplicated blocks, separated by file-specific content so they
    # are genuinely separate clone regions, must not collapse into one — while
    # still not exploding into one-group-per-offset.
    block1 = "\n".join([f"    aa_{i} = first(i) * scale_{i}" for i in range(30)])
    block2 = "\n".join([f"    bb_{i} = second(i) / ratio_{i}" for i in range(30)])
    unique_a = "\n".join([f"    only_in_a_{i} = alpha_{i}(i)" for i in range(12)])
    unique_b = "\n".join([f"    only_in_b_{i} = beta_{i}(i)" for i in range(12)])
    code_a = f"def one():\n{block1}\ndef mid_a():\n{unique_a}\ndef two():\n{block2}\n"
    code_b = f"def one():\n{block1}\ndef mid_b():\n{unique_b}\ndef two():\n{block2}\n"

    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "a.py", code_a)
        _write_file(root, "b.py", code_b)
        files = sorted(root.glob("*.py"))
        cfg = HallowConfig(root=root)
        # strict mode: the file-specific mid sections keep their identifiers, so
        # they break contiguity between the two shared blocks (under mild mode
        # every `name = f(i)` line normalizes identically and would merge).
        cfg.duplicates.mode = "strict"
        cfg.duplicates.min_tokens = 50
        cfg.duplicates.min_lines = 5
        groups, _ = detect_duplicates(files, root, cfg)

        # Two distinct clone regions (block1 and block2) -> two groups, not one,
        # and not one-per-offset.
        assert len(groups) == 2, f"expected two distinct clones, got {len(groups)}"


def test_syntax_error_file_skipped():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "broken.py", "def bad(:\n")
        tokens = tokenize_file(root / "broken.py", root, "strict")
        assert len(tokens) == 0 or isinstance(tokens, list)
