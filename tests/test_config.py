"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config import HallowConfig, load_config


def test_default_config():
    cfg = HallowConfig()
    assert cfg.format == "human"
    assert cfg.ci is False
    assert len(cfg.ignore_patterns) > 0


def test_load_from_pyproject():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pyproject = root / "pyproject.toml"
        pyproject.write_text("""
[tool.hallow]
entry = ["src/main.py"]

[tool.hallow.rules]
unused-files = "warn"
unused-imports = "off"
""")
        cfg = load_config(root=root)
        assert cfg.entry == ["src/main.py"]
        assert cfg.rules.overrides["unused-files"] == "warn"
        assert cfg.rules.overrides["unused-imports"] == "off"


def test_load_from_hallowrc():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        hallowrc = root / ".hallowrc.toml"
        hallowrc.write_text("""
entry = ["app/**/*.py"]
ignore_patterns = ["**/migrations/**"]
""")
        cfg = load_config(root=root)
        assert cfg.entry == ["app/**/*.py"]
        assert "**/migrations/**" in cfg.ignore_patterns


def test_overrides():
    cfg = load_config(overrides={"ci": True, "format": "json"})
    assert cfg.ci is True
    assert cfg.format == "json"
