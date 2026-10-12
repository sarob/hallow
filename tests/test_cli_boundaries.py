"""Tests for `hallow check --boundaries`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hallow.cli.main import app


def test_boundaries_preset_reports_violation(tmp_path: Path):
    (tmp_path / "models").mkdir()
    (tmp_path / "routes").mkdir()
    (tmp_path / "models" / "user.py").write_text("from routes.api import handler\nhandler()\n")
    (tmp_path / "routes" / "api.py").write_text("def handler(): ...\n")
    result = CliRunner().invoke(
        app, ["check", "--root", str(tmp_path), "--boundaries", "layered", "-f", "compact"]
    )
    assert "boundary-violation" in result.output


def test_unknown_preset_is_rejected(tmp_path: Path):
    result = CliRunner().invoke(app, ["check", "--root", str(tmp_path), "--boundaries", "nope"])
    assert result.exit_code == 2
