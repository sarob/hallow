"""Tests for the CLI contract: exit codes, JSON documents, error documents, schema."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hallow.cli.main import app
from hallow.output.envelope import output_json_schema

runner = CliRunner()


def _project(tmp_path: Path, helper: str = "import json\ndef run(): ...\n") -> Path:
    (tmp_path / "main.py").write_text("import helper\nhelper.run()\n")
    (tmp_path / "helper.py").write_text(helper)
    return tmp_path


def _json(args: list[str]) -> tuple[int, dict]:
    result = runner.invoke(app, args)
    return result.exit_code, json.loads(result.stdout)


def test_error_findings_exit_1_without_any_flag(tmp_path: Path):
    # unused-imports is error-severity by default
    code, doc = _json(["check", "--root", str(_project(tmp_path)), "-f", "json"])
    assert code == 1
    assert doc["gate_outcomes"]["findings"] == {
        "status": "fail",
        "enforced": True,
        "observed": 1,
        "threshold": None,
    }


def test_clean_project_exits_0(tmp_path: Path):
    code, doc = _json(
        ["check", "--root", str(_project(tmp_path, "def run(): ...\n")), "-f", "json"]
    )
    assert code == 0
    assert doc["gate_outcomes"]["findings"]["status"] == "pass"


def test_warn_findings_exit_0_unless_fail_on_issues(tmp_path: Path):
    root = _project(tmp_path, "def run(): ...\nLIMIT = 3\n")
    (root / "main.py").write_text("from helper import run\nrun()\n")
    # unused-variables is warn-severity
    code, doc = _json(["check", "--root", str(root), "-f", "json"])
    assert (code, doc["gate_outcomes"]["findings"]["status"]) == (0, "warn")
    code, _ = _json(["check", "--root", str(root), "-f", "json", "--fail-on-issues"])
    assert code == 1


def test_json_document_root_fields(tmp_path: Path):
    _, doc = _json(["check", "--root", str(_project(tmp_path)), "-f", "json"])
    assert doc["kind"] == "check"
    assert doc["schema_version"] == 1
    assert isinstance(doc["version"], str)
    assert isinstance(doc["elapsed_ms"], int)
    assert set(doc["summary"]) == {"total_files_scanned", "total_issues", "errors", "warnings"}
    finding = doc["findings"][0]
    assert finding["finding_id"].startswith("hl1:unused-imports:")
    assert "fix" not in finding
    types = [a["type"] for a in finding["actions"]]
    assert types == ["remove-import", "suppress-line"]
    assert finding["actions"][0]["auto_fixable"] is True
    assert finding["actions"][1]["comment"] == "# hallow-ignore unused-imports"
    assert doc["next_steps"][0] == {
        "id": "preview-fixes",
        "command": "hallow fix --dry-run",
        "reason": "Some findings are auto-fixable; preview the changes",
    }


def test_ci_is_sarif_fail_on_issues_quiet_and_explicit_format_wins(tmp_path: Path):
    root = _project(tmp_path)
    result = runner.invoke(app, ["check", "--root", str(root), "--ci"])
    assert json.loads(result.stdout)["version"] == "2.1.0"  # SARIF
    assert result.exit_code == 1
    result = runner.invoke(app, ["check", "--root", str(root), "--ci", "-f", "github"])
    assert result.stdout.startswith("::error")


def test_json_error_document_on_stdout(tmp_path: Path):
    code, doc = _json(["check", "--root", str(tmp_path), "-f", "json", "-c", "missing.toml"])
    assert code == 3
    assert doc == {
        "error": True,
        "message": "config file not found: missing.toml",
        "exit_code": 3,
        "code": "HALLOW_CONFIG_NOT_FOUND",
    }


def test_invalid_input_exits_2(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("rules = [")
    assert runner.invoke(app, ["check", "--root", str(tmp_path), "-c", str(bad)]).exit_code == 2
    result = runner.invoke(app, ["check", "--root", str(tmp_path), "-f", "yaml"])
    assert result.exit_code == 2
    assert "unknown format 'yaml'" in result.stderr
    assert runner.invoke(app, ["dupes", "--root", str(tmp_path), "-m", "fuzzy"]).exit_code == 2


def test_stale_baseline_gate_enforced_only_with_flag(tmp_path: Path):
    root = _project(tmp_path)
    b = tmp_path / "b.json"
    runner.invoke(app, ["check", "--root", str(root), "--save-baseline", str(b), "-q"])
    (root / "helper.py").write_text("def run(): ...\n")  # the baselined finding is gone

    args = ["check", "--root", str(root), "--baseline", str(b), "-f", "json"]
    code, doc = _json(args)
    assert code == 0
    assert doc["gate_outcomes"]["stale-baseline"]["status"] == "fail"
    assert doc["gate_outcomes"]["stale-baseline"]["enforced"] is False
    code, _ = _json([*args, "--fail-on-stale-baseline"])
    assert code == 1


def test_quiet_silences_status_but_not_output(tmp_path: Path):
    root = _project(tmp_path)
    b = tmp_path / "b.json"
    loud = runner.invoke(
        app, ["check", "--root", str(root), "--save-baseline", str(b), "-f", "json"]
    )
    quiet = runner.invoke(
        app, ["check", "--root", str(root), "--save-baseline", str(b), "-f", "json", "-q"]
    )
    assert "Saved baseline" in loud.stderr
    assert quiet.stderr == ""
    assert json.loads(quiet.stdout)["kind"] == "check"


def test_health_and_dupes_documents(tmp_path: Path):
    root = _project(tmp_path)
    code, doc = _json(["health", "--root", str(root), "-f", "json"])
    assert (code, doc["kind"], doc["schema_version"]) == (0, "health", 1)
    code, doc = _json(["dupes", "--root", str(root), "-f", "json"])
    assert (code, doc["kind"], doc["mode"]) == (0, "dupes", "mild")


def test_schema_manifest_lists_commands_flags_and_exit_codes():
    result = runner.invoke(app, ["schema"])
    doc = json.loads(result.stdout)
    assert doc["manifest_version"] == "1"
    names = {c["name"] for c in doc["commands"]}
    assert {"check", "audit", "health", "dupes", "security", "baselines prune", "schema"} <= names
    assert set(doc["exit_codes"]) == {"0", "1", "2", "3"}
    check = next(c for c in doc["commands"] if c["name"] == "check")
    flags = {f["name"]: f for f in check["flags"]}
    assert flags["--fail-on-issues"]["type"] == "bool"
    assert flags["--format"]["short"] == "-f"
    assert {r["id"] for r in doc["issue_types"]} >= {"unused-imports", "stale-suppression"}


def test_committed_output_schema_is_current():
    committed = Path(__file__).parent.parent / "docs" / "output-schema.json"
    assert json.loads(committed.read_text()) == output_json_schema(), (
        "docs/output-schema.json is stale: run `hallow schema --output-schema > "
        "docs/output-schema.json`"
    )
