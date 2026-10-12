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
