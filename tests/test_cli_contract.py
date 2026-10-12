"""Tests for the CLI contract: exit codes, JSON documents, error documents, schema."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hallow.cli.main import app
from hallow.output.envelope import output_json_schema

runner = CliRunner()


def _project(tmp_path: Path, helper: str = "import json\ndef run(): ...\n") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    assert doc["next_steps"][0]["id"] == "preview-fixes"


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


# ── review of #22 ──

import shlex  # noqa: E402

ENVELOPE_FIELDS = {"kind", "schema_version", "version", "elapsed_ms", "gate_outcomes", "next_steps"}


def test_next_steps_run_as_written_from_another_directory(tmp_path: Path, monkeypatch):
    # commands carry --root/--config and are shell-quoted (a baseline path with a space)
    root = _project(tmp_path / "proj")
    cfg = tmp_path / "my config.toml"
    cfg.write_text('[rules]\nhigh-complexity = "warn"\n')
    b = tmp_path / "base line.json"
    runner.invoke(app, ["check", "--root", str(root), "-c", str(cfg), "--save-baseline", str(b)])
    (root / "helper.py").write_text("def run(): ...\n")
    code, doc = _json(
        ["check", "--root", str(root), "-c", str(cfg), "--baseline", str(b), "-f", "json"]
    )
    steps = {s["id"]: s["command"] for s in doc["next_steps"]}
    assert "prune-baseline" in steps
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for command in steps.values():
        argv = shlex.split(command)
        assert argv[0] == "hallow" and "--root" in argv and "--config" in argv
        result = runner.invoke(app, argv[1:])
        assert result.exit_code in (0, 1), (command, result.output)


def test_ci_fails_on_warnings_only(tmp_path: Path):
    root = _project(tmp_path, "def run(): ...\nLIMIT = 3\n")
    (root / "main.py").write_text("from helper import run\nrun()\n")  # only unused-variables (warn)
    assert runner.invoke(app, ["check", "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, ["check", "--root", str(root), "--ci"]).exit_code == 1


def test_every_document_kind_has_the_envelope_fields(tmp_path: Path):
    root = _project(tmp_path / "p")
    b = tmp_path / "b.json"
    runner.invoke(app, ["check", "--root", str(root), "--save-baseline", str(b), "-q"])
    for args in (
        ["check"], ["security"], ["health"], ["dupes"],
        ["baselines", "prune", "--baseline", str(b), "--check"],
    ):  # fmt: skip
        _, doc = _json([*args, "--root", str(root), "-f", "json"])
        assert set(doc) >= ENVELOPE_FIELDS, (args, set(doc))
    _, prune = _json(
        ["baselines", "prune", "--baseline", str(b), "--check", "--root", str(root), "-f", "json"]
    )
    assert prune["gate_outcomes"]["prune-check"]["enforced"] is True


def test_io_errors_are_json_error_documents(tmp_path: Path):
    root = _project(tmp_path / "p")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, doc = _json(["check", "--root", str(root), "-f", "json", "-o", str(out_dir)])
    assert code == 2
    assert doc["error"] is True and doc["code"] == "HALLOW_IO_ERROR"


def test_refused_prune_is_an_error_document(tmp_path: Path):
    bad = tmp_path / "b.json"
    bad.write_text('{"version": 1, "findings": []}')
    code, doc = _json(
        ["baselines", "prune", "--baseline", str(bad), "--root", str(tmp_path), "-f", "json"]
    )
    assert (code, doc["code"]) == (2, "HALLOW_BASELINE_REFUSED")


def test_mcp_analyze_reports_the_findings_gate(tmp_path: Path):
    from hallow.mcp.server import _dispatch_tool

    doc = _dispatch_tool("analyze", {"root": str(_project(tmp_path))})
    assert doc["gate_outcomes"]["findings"]["status"] == "fail"


def test_dupes_honours_suppressions(tmp_path: Path):
    block = "\n".join(f"    v{i} = compute({i}) + offset_{i}" for i in range(20))
    (tmp_path / "a.py").write_text(f"# hallow-ignore-file duplicate-code\ndef fa():\n{block}\n")
    (tmp_path / "b.py").write_text(f"# hallow-ignore-file duplicate-code\ndef fb():\n{block}\n")
    code, doc = _json(
        ["dupes", "--root", str(tmp_path), "--min-tokens", "30", "-f", "json", "--fail-on-issues"]
    )
    assert doc["findings"] == [] and code == 0
    assert doc["clone_groups"]  # the data is still reported


def test_manifest_publishes_allowed_values():
    doc = json.loads(runner.invoke(app, ["schema"]).stdout)
    flags = {c["name"]: {f["name"]: f for f in c["flags"]} for c in doc["commands"]}
    assert flags["dupes"]["--mode"]["possible_values"] == ["strict", "mild", "weak", "semantic"]
    assert "sarif" in flags["check"]["--format"]["possible_values"]
    assert flags["health"]["--format"]["possible_values"] == ["human", "json"]
