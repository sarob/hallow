"""Tests for `# hallow-ignore` suppression comments and stale-suppression."""

from __future__ import annotations

import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig
from hallow.core import analyze
from hallow.extract import extract_module


def _analyze(files: dict[str, str]) -> list[tuple[str, str, int]]:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, src in files.items():
            (root / name).write_text(textwrap.dedent(src))
        results = analyze(HallowConfig(root=root))
        return [(f.rule.value, f.location.file, f.location.line) for f in results.findings]


def test_parses_three_kinds_with_rules_and_reason(tmp_path):
    src = textwrap.dedent("""\
        # hallow-ignore-file unused-files
        import os  # hallow-ignore unused-imports, unused-variables -- side effect
        # hallow-ignore-next-line

        def f(): ...
        s = "# hallow-ignore not-a-comment"
    """)
    path = tmp_path / "m.py"
    path.write_text(src)
    info = extract_module(path, tmp_path)
    got = [(s.kind, s.line, s.target_line, s.rules, s.reason) for s in info.suppressions]
    assert got == [
        ("file", 1, None, ["unused-files"], None),
        ("line", 2, 2, ["unused-imports", "unused-variables"], "side effect"),
        ("next-line", 3, 5, [], None),
    ]


def test_line_suppression_hides_finding():
    found = _analyze(
        {
            "main.py": "import helper\nhelper.run()\n",
            "helper.py": """\
            import json  # hallow-ignore unused-imports
            def run(): ...
        """,
        }
    )
    assert ("unused-imports", "helper.py", 1) not in found
    assert not [f for f in found if f[0] == "stale-suppression"]


def test_next_line_suppression_hides_finding():
    found = _analyze(
        {
            "main.py": "from helper import run\nrun()\n",
            "helper.py": """\
            def run(): ...

            # hallow-ignore-next-line unused-functions -- called by name
            def nightly(): ...
        """,
        }
    )
    assert not [f for f in found if f[0] in ("unused-functions", "stale-suppression")]


def test_suppression_for_other_rule_does_not_hide_and_is_stale():
    found = _analyze(
        {
            "main.py": "import helper\nhelper.run()\n",
            "helper.py": """\
            import json  # hallow-ignore unused-variables
            def run(): ...
        """,
        }
    )
    assert ("unused-imports", "helper.py", 1) in found
    assert ("stale-suppression", "helper.py", 1) in found


def test_unknown_rule_is_reported_stale():
    found = _analyze({"main.py": "x = 1  # hallow-ignore no-such-rule\nprint(x)\n"})
    assert ("stale-suppression", "main.py", 1) in found


def test_file_suppression_hides_every_finding_of_its_rules():
    found = _analyze(
        {
            "main.py": "import helper\nhelper.run()\n",
            "helper.py": """\
            # hallow-ignore-file unused-imports
            import json
            import re
            def run(): ...
        """,
        }
    )
    assert not [f for f in found if f[1] == "helper.py"]


def test_suppression_naming_only_disabled_rules_is_inert(tmp_path):
    (tmp_path / "main.py").write_text("x = 1  # hallow-ignore taint-sink\nprint(x)\n")
    # taint-sink is off by default
    results = analyze(HallowConfig(root=tmp_path))
    assert not [f for f in results.findings if f.rule.value == "stale-suppression"]


def test_unknown_rule_next_to_a_valid_one_is_still_reported():
    # Review of #15: the valid rule hid the typo.
    found = _analyze(
        {
            "main.py": "import helper\nhelper.run()\n",
            "helper.py": """\
            import json  # hallow-ignore unused-imports, no-such-rule
            def run(): ...
        """,
        }
    )
    assert ("unused-imports", "helper.py", 1) not in found
    assert ("stale-suppression", "helper.py", 1) in found


def test_stale_findings_are_suppressible_by_another_suppression():
    # Review of #15: stale-suppression findings could not be suppressed.
    found = _analyze(
        {
            "main.py": """\
            # hallow-ignore-file stale-suppression
            x = 1  # hallow-ignore no-such-rule
            print(x)
        """,
        }
    )
    assert not [f for f in found if f[0] == "stale-suppression"]


def test_a_suppression_cannot_hide_its_own_staleness():
    found = _analyze({"main.py": "# hallow-ignore-file\nprint(1)\n"})
    assert ("stale-suppression", "main.py", 1) in found


def test_security_command_honours_suppressions(tmp_path):
    from typer.testing import CliRunner

    from hallow.cli.main import app

    (tmp_path / "app.py").write_text(
        'TOKEN = "ghp_' + "a" * 36 + '"  # hallow-ignore hardcoded-secret -- test fixture\n'
    )
    result = CliRunner().invoke(app, ["security", "--root", str(tmp_path), "-f", "compact"])
    assert "hardcoded-secret" not in result.output
