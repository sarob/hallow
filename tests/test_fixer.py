"""Tests for auto-fix engine."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.core.fixer import FixResult, apply_fixes
from hallow.types import (
    AnalysisResults,
    Finding,
    FixAction,
    Location,
    RuleId,
    Severity,
)


def _make_findings(
    rule: RuleId,
    file: str,
    line: int,
    fix_kind: str,
    fix_target: str,
) -> Finding:
    return Finding(
        rule=rule,
        severity=Severity.ERROR,
        message=f"Unused: {fix_target}",
        location=Location(file=file, line=line),
        fix=FixAction(kind=fix_kind, target=fix_target),
    )


def test_dry_run_no_changes():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "app.py"
        src.write_text("import os\nimport sys\nprint('hello')\n")

        results = AnalysisResults(
            findings=[
                _make_findings(RuleId.UNUSED_IMPORTS, "app.py", 1, "remove_import", "os"),
            ]
        )
        fix = apply_fixes(results, root, dry_run=True)
        assert fix.total > 0
        assert src.read_text() == "import os\nimport sys\nprint('hello')\n"


def test_apply_removes_import():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "app.py"
        src.write_text("import os\nimport sys\nprint(sys.argv)\n")

        results = AnalysisResults(
            findings=[
                _make_findings(RuleId.UNUSED_IMPORTS, "app.py", 1, "remove_import", "os"),
            ]
        )
        fix = apply_fixes(results, root, dry_run=False)
        assert len(fix.removed_imports) == 1
        content = src.read_text()
        assert "import os" not in content
        assert "import sys" in content


def test_apply_deletes_file():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        dead = root / "dead.py"
        dead.write_text("# dead code\n")

        results = AnalysisResults(
            findings=[
                _make_findings(RuleId.UNUSED_FILES, "dead.py", 1, "delete_file", "dead.py"),
            ]
        )
        fix = apply_fixes(results, root, dry_run=False)
        assert len(fix.deleted_files) == 1
        assert not dead.exists()


def test_dry_run_delete_keeps_file():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        dead = root / "dead.py"
        dead.write_text("# dead code\n")

        results = AnalysisResults(
            findings=[
                _make_findings(RuleId.UNUSED_FILES, "dead.py", 1, "delete_file", "dead.py"),
            ]
        )
        fix = apply_fixes(results, root, dry_run=True)
        assert len(fix.deleted_files) == 1
        assert dead.exists()


def test_fix_result_total():
    fr = FixResult()
    fr.removed_imports = ["a", "b"]
    fr.deleted_files = ["c"]
    fr.removed_deps = ["d"]
    assert fr.total == 4


def test_no_findings_nothing_to_fix():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        results = AnalysisResults(findings=[])
        fix = apply_fixes(results, root, dry_run=False)
        assert fix.total == 0
