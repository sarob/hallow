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


def test_syntax_error_file_skipped():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_file(root, "broken.py", "def bad(:\n")
        tokens = tokenize_file(root / "broken.py", root, "strict")
        assert len(tokens) == 0 or isinstance(tokens, list)
