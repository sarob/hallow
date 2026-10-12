"""Tests for `hallow check --boundaries`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hallow.cli.main import app


def _flat(output: str) -> str:
    """Error text without rich's box border and line wrapping."""
    return " ".join(output.replace("│", " ").split())


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
    text = _flat(result.output)
    assert "Invalid value for '--boundaries'" in text
    assert "layered, hexagonal, feature-sliced" in text


def test_preset_replaces_configured_zones(tmp_path: Path):
    # A configured zone set allows models -> routes; --boundaries layered replaces it.
    (tmp_path / "models").mkdir()
    (tmp_path / "routes").mkdir()
    (tmp_path / "models" / "user.py").write_text("from routes.api import handler\nhandler()\n")
    (tmp_path / "routes" / "api.py").write_text("def handler(): ...\n")
    (tmp_path / ".hallowrc.toml").write_text(
        "[boundaries]\n"
        'zones = [{name = "m", patterns = ["**/models/**"]}, '
        '{name = "r", patterns = ["**/routes/**"]}]\n'
        'rules = [{from = "m", allow = ["r"]}, {from = "r", allow = []}]\n'
    )
    runner = CliRunner()
    configured = runner.invoke(app, ["check", "--root", str(tmp_path), "-f", "compact"])
    assert "boundary-violation" not in configured.output
    preset = runner.invoke(
        app, ["check", "--root", str(tmp_path), "--boundaries", "layered", "-f", "compact"]
    )
    assert "boundary-violation" in preset.output


def test_root_options_before_a_command_are_refused(tmp_path: Path):
    # Review of #14: `hallow --boundaries layered check` used to drop the option.
    result = CliRunner().invoke(app, ["--boundaries", "layered", "check", "--root", str(tmp_path)])
    assert result.exit_code == 2
    assert "--boundaries given before 'check'" in _flat(result.output)
    # the bare command still takes them
    bare = CliRunner().invoke(app, ["--root", str(tmp_path), "--boundaries", "layered"])
    assert bare.exit_code == 0
