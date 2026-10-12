"""Tests for baselines: canonical-key entries, staleness, prune."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hallow.cli.main import app
from hallow.core.baseline import filter_baseline, load_baseline, prune_baseline, save_baseline
from hallow.identity import stamp_identities
from hallow.types import AnalysisResults, Finding, Location, RuleId, Severity


def _finding(file: str, line: int, symbol: str) -> Finding:
    return Finding(
        rule=RuleId.UNUSED_IMPORTS,
        severity=Severity.ERROR,
        message=f"'{symbol}' unused",
        location=Location(file=file, line=line),
        symbol=symbol,
    )


def _results(*findings: Finding) -> AnalysisResults:
    stamp_identities(list(findings))
    r = AnalysisResults(findings=list(findings))
    r.compute_totals()
    return r


def test_saved_file_records_kind_identity_and_keys(tmp_path: Path):
    path = tmp_path / "b.json"
    assert save_baseline(_results(_finding("a.py", 1, "os"), _finding("b.py", 5, "sys")), path) == 2
    data = json.loads(path.read_text())
    assert data["kind"] == "check"
    assert data["identity"] == "hl1"
    assert data["entries"] == ["unused-imports:a.py:os", "unused-imports:b.py:sys"]


def test_moved_code_stays_baselined(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(_results(_finding("a.py", 1, "os")), path)
    # same import, 40 lines lower, plus a genuinely new finding
    filtered = filter_baseline(
        _results(_finding("a.py", 41, "os"), _finding("c.py", 3, "json")), path
    )
    assert [f.symbol for f in filtered.findings] == ["json"]
    assert filtered.baseline_staleness.matched_entries == 1
    assert filtered.baseline_staleness.stale is False


def test_n_entries_hide_at_most_n_occurrences(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(_results(_finding("a.py", 1, "os")), path)
    filtered = filter_baseline(_results(_finding("a.py", 1, "os"), _finding("a.py", 9, "os")), path)
    assert [f.location.line for f in filtered.findings] == [9]


def test_stale_entries_reported(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(_results(_finding("a.py", 1, "os"), _finding("a.py", 2, "re")), path)
    st = filter_baseline(_results(_finding("a.py", 1, "os")), path).baseline_staleness
    assert (st.baseline_entries, st.matched_entries, st.stale_entries) == (2, 1, 1)
    assert st.stale is True
    assert st.warning == "partial"


def test_line_keyed_legacy_file_is_unrecognised_and_suppresses_nothing(tmp_path: Path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"version": 1, "findings": ["unused-imports:a.py:1:'os' unused"]}))
    filtered = filter_baseline(_results(_finding("a.py", 1, "os")), path)
    assert len(filtered.findings) == 1
    assert filtered.baseline_staleness.unrecognised_format is True


def test_missing_and_corrupt_files(tmp_path: Path):
    assert load_baseline(tmp_path / "none.json").recognised is True
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert load_baseline(bad).recognised is False


def test_prune_removes_only_unmatched_entries(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(_results(_finding("a.py", 1, "os"), _finding("a.py", 2, "re")), path)
    current = _results(_finding("a.py", 1, "os"), _finding("z.py", 1, "new"))

    item = prune_baseline(current, path, check=True)
    assert item["status"] == "would-prune"
    assert json.loads(path.read_text())["entries"] == [
        "unused-imports:a.py:os",
        "unused-imports:a.py:re",
    ]

    item = prune_baseline(current, path, check=False)
    assert item["status"] == "pruned"
    assert item["removed"] == [{"key": "unused-imports:a.py:re", "count": 1}]
    # never adds the new finding
    assert json.loads(path.read_text())["entries"] == ["unused-imports:a.py:os"]


def test_cli_check_baseline_round_trip_and_prune(tmp_path: Path):
    (tmp_path / "main.py").write_text("import helper\nhelper.run()\n")
    (tmp_path / "helper.py").write_text("import json\nimport re\ndef run(): ...\n")
    b = tmp_path / "baseline.json"
    runner = CliRunner()

    runner.invoke(app, ["check", "--root", str(tmp_path), "--save-baseline", str(b), "-f", "json"])
    out = runner.invoke(app, ["check", "--root", str(tmp_path), "--baseline", str(b), "-f", "json"])
    data = json.loads(out.stdout)
    assert data["findings"] == []
    assert data["baseline_staleness"]["matched_entries"] == 2

    # fix one finding: its entry goes stale, prune --check reports it, prune removes it
    (tmp_path / "helper.py").write_text("import json\ndef run(): ...\n")
    assert (
        runner.invoke(
            app, ["baselines", "prune", "--root", str(tmp_path), "--baseline", str(b), "--check"]
        ).exit_code
        == 1
    )
    assert (
        runner.invoke(
            app, ["baselines", "prune", "--root", str(tmp_path), "--baseline", str(b)]
        ).exit_code
        == 0
    )
    assert json.loads(b.read_text())["entries"] == ["unused-imports:helper.py:json"]


def test_fully_stale_baseline_warns_on_a_clean_full_run(tmp_path: Path):
    # Review of #21: every finding fixed -> every entry stale, and still a warning.
    root = tmp_path / "p"
    root.mkdir()
    (root / "main.py").write_text("import helper\nhelper.run()\n")
    (root / "helper.py").write_text("import json\ndef run(): ...\n")
    b = tmp_path / "b.json"
    runner = CliRunner()
    runner.invoke(app, ["check", "--root", str(root), "--save-baseline", str(b)])
    (root / "helper.py").write_text("def run(): ...\n")
    result = runner.invoke(app, ["check", "--root", str(root), "--baseline", str(b)])
    assert "is stale: 1 of 1 entries match no finding" in result.stderr
    st = filter_baseline(_results(), b).baseline_staleness
    assert (st.warning, st.stale_entries) == ("zero-overlap", 1)


def test_change_scoped_filter_never_warns(tmp_path: Path):
    path = tmp_path / "b.json"
    save_baseline(_results(_finding("unchanged.py", 1, "os")), path)
    st = filter_baseline(_results(), path, change_scoped=True).baseline_staleness
    assert st.stale_entries == 1 and st.warning == "none"


def test_non_utf8_baseline_is_unrecognised_not_a_crash(tmp_path: Path):
    # Review of #21, round 3.
    bad = tmp_path / "b.json"
    bad.write_bytes(b"\xff\xfe not json")
    assert load_baseline(bad).recognised is False
    filtered = filter_baseline(_results(_finding("a.py", 1, "os")), bad)
    assert len(filtered.findings) == 1 and filtered.baseline_staleness.unrecognised_format
    assert prune_baseline(_results(), bad, check=True)["status"] == "refused"
    runner = CliRunner()
    result = runner.invoke(
        app, ["baselines", "prune", "--baseline", str(bad), "--root", str(tmp_path)]
    )
    assert result.exit_code == 2
