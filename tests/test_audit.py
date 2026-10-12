"""Tests for the audit gate, change scoping and the baseline-growth gate (real git repos)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hallow.cli.main import app

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("HALLOW_AUDIT_BASE", raising=False)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "main.py").write_text("import helper\nhelper.run()\n")
    # an inherited error-severity finding: unused import in helper.py
    (tmp_path / "helper.py").write_text("import json\n\ndef run(): ...\n")
    (tmp_path / "other.py").write_text("import helper\nhelper.run()\n")
    _commit(tmp_path, "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _audit(repo: Path, *extra: str) -> tuple[int, dict]:
    r = runner.invoke(app, ["audit", "--root", str(repo), "--base", "main", "-f", "json", *extra])
    return r.exit_code, json.loads(r.stdout)


def test_inherited_finding_in_touched_file_passes(repo: Path):
    (repo / "helper.py").write_text("import json\n\n# a comment\ndef run(): ...\n")
    code, doc = _audit(repo)
    assert (code, doc["verdict"]) == (0, "pass")
    [f] = doc["findings"]
    assert (f["rule"], f["introduced"]) == ("unused-imports", False)
    assert doc["attribution"]["dead_code_inherited"] == 1
    assert doc["next_steps"][0]["id"] == "audit-all"
    # the shared document envelope, and a next step that targets this project
    assert {"kind", "schema_version", "version", "elapsed_ms", "gate_outcomes"} <= set(doc)
    assert "--root" in doc["next_steps"][0]["command"]


def test_introduced_finding_fails(repo: Path):
    (repo / "helper.py").write_text("import json\nimport re\n\ndef run(): ...\n")
    code, doc = _audit(repo)
    assert (code, doc["verdict"]) == (1, "fail")
    new = [f for f in doc["findings"] if f["introduced"]]
    assert [f["symbol"] for f in new] == ["re"]


def test_gate_all_enforces_inherited(repo: Path):
    (repo / "helper.py").write_text("import json\n\n# touched\ndef run(): ...\n")
    code, doc = _audit(repo, "--gate", "all")
    assert (code, doc["verdict"], doc["base_snapshot_skipped"]) == (1, "fail", True)
    assert doc["findings"][0]["introduced"] is None


def test_findings_outside_changed_files_are_out_of_scope(repo: Path):
    (repo / "other.py").write_text("import helper\nhelper.run()\n# edit\n")
    code, doc = _audit(repo)
    assert (code, doc["changed_files"], doc["findings"]) == (0, ["other.py"], [])


def test_moved_lines_stay_inherited(repo: Path):
    (repo / "helper.py").write_text("\n" * 30 + "import json\n\ndef run(): ...\n")
    code, doc = _audit(repo)
    assert code == 0
    assert doc["findings"][0]["introduced"] is False


def test_pure_rename_stays_inherited(repo: Path):
    _git(repo, "mv", "helper.py", "helpers.py")
    for name in ("main.py", "other.py"):
        (repo / name).write_text("import helpers\nhelpers.run()\n")
    _commit(repo, "rename")
    code, doc = _audit(repo)
    unused = [f for f in doc["findings"] if f["rule"] == "unused-imports"]
    assert unused and all(f["introduced"] is False for f in unused)
    assert code == 0


def test_uncommitted_and_untracked_changes_count(repo: Path):
    (repo / "fresh.py").write_text("import os\n")  # untracked, unused import + unused file
    code, doc = _audit(repo)
    assert "fresh.py" in doc["changed_files"]
    assert code == 1


def test_docs_only_change_skips_base(repo: Path):
    (repo / "README.md").write_text("docs\n")
    code, doc = _audit(repo)
    assert (code, doc["base_snapshot_skipped"], doc["findings"]) == (0, True, [])


def test_higher_complexity_is_introduced(repo: Path):
    def body(n: int) -> str:
        return "def run(x):\n" + "".join(
            f"    if x == {i}:\n        return {i}\n" for i in range(n)
        )

    (repo / "helper.py").write_text("import json\n\n" + body(22))
    _commit(repo, "complex")
    _git(repo, "branch", "-f", "main", "HEAD")
    (repo / "helper.py").write_text("import json\n\n" + body(22) + "# touch\n")
    code, doc = _audit(repo)
    cx = [f for f in doc["findings"] if f["rule"] == "high-complexity"]
    assert cx and all(f["introduced"] is False for f in cx)
    (repo / "helper.py").write_text("import json\n\n" + body(30))
    code, doc = _audit(repo)
    cx = [f for f in doc["findings"] if f["rule"] == "high-complexity"]
    assert cx and all(f["introduced"] for f in cx)


def test_base_detection_and_failure(repo: Path, tmp_path_factory, monkeypatch):
    (repo / "helper.py").write_text("import json\nimport re\n\ndef run(): ...\n")
    r = runner.invoke(app, ["audit", "--root", str(repo), "-f", "json"])
    doc = json.loads(r.stdout)
    assert doc["base_description"] == "merge-base with main"

    monkeypatch.setenv("HALLOW_AUDIT_BASE", "nope")
    r = runner.invoke(app, ["audit", "--root", str(repo), "-f", "json"])
    assert r.exit_code == 2
    assert json.loads(r.stdout)["code"] == "HALLOW_BAD_BASE"

    lonely = tmp_path_factory.mktemp("lonely")
    _git(lonely, "init", "-q", "-b", "trunk")
    (lonely / "a.py").write_text("x = 1\n")
    _git(
        lonely,
        "-c",
        "user.email=t@e",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "i",
    )
    monkeypatch.delenv("HALLOW_AUDIT_BASE")
    r = runner.invoke(app, ["audit", "--root", str(lonely), "-f", "json"])
    assert r.exit_code == 2
    assert json.loads(r.stdout)["code"] == "HALLOW_NO_BASE"


def test_audit_leaves_no_worktree_behind(repo: Path):
    (repo / "helper.py").write_text("import json\nimport re\n\ndef run(): ...\n")
    _audit(repo)
    assert _git(repo, "worktree", "list").count("\n") == 1


def test_check_changed_since_scopes_findings(repo: Path):
    (repo / "other.py").write_text("import helper\nimport os\nhelper.run()\n")
    r = runner.invoke(
        app, ["check", "--root", str(repo), "--changed-since", "main", "-f", "json", "-q"]
    )
    doc = json.loads(r.stdout)
    assert {f["location"]["file"] for f in doc["findings"]} == {"other.py"}


def test_baseline_growth_gate(repo: Path):
    b = repo / "hallow-baseline.json"
    _git(repo, "checkout", "-q", "main")
    runner.invoke(app, ["check", "--root", str(repo), "--save-baseline", str(b), "-q"])
    _commit(repo, "baseline")
    _git(repo, "checkout", "-q", "-b", "grow")

    args = ["check", "--root", str(repo), "--baseline", str(b), "-f", "json", "-q"]
    args += ["--fail-on-baseline-growth", "--baseline-base", "main"]
    (repo / "helper.py").write_text("import json\nimport re\n\ndef run(): ...\n")
    _commit(repo, "edit")
    code, doc = (lambda r: (r.exit_code, json.loads(r.stdout)))(runner.invoke(app, args))
    assert doc["gate_outcomes"]["baseline-growth"]["status"] == "pass"

    runner.invoke(app, ["check", "--root", str(repo), "--save-baseline", str(b), "-q"])
    r = runner.invoke(app, args)
    assert json.loads(r.stdout)["gate_outcomes"]["baseline-growth"] == {
        "status": "fail",
        "enforced": True,
        "observed": 1,
        "threshold": None,
    }
    assert r.exit_code == 1


def test_change_scoped_runs_do_not_warn_about_unchanged_baseline_entries(repo: Path):
    # Review of #21: entries for files outside the change are not stale.
    b = repo / "b.json"
    runner.invoke(app, ["check", "--root", str(repo), "--save-baseline", str(b), "-q"])
    _commit(repo, "baseline")
    (repo / "other.py").write_text("import helper\nhelper.run()\n# edit\n")
    for args in (
        ["check", "--root", str(repo), "--changed-since", "main", "--baseline", str(b)],
        ["audit", "--root", str(repo), "--base", "main", "--baseline", str(b)],
    ):
        result = runner.invoke(app, args)
        assert "is stale" not in result.stderr, args
        assert result.exit_code in (0, 1)
    # the same baseline on a full run with that finding fixed does warn
    (repo / "helper.py").write_text("def run(): ...\n")
    full = runner.invoke(app, ["check", "--root", str(repo), "--baseline", str(b)])
    assert "is stale" in full.stderr
