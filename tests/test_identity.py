"""Tests for stable finding identity."""

from __future__ import annotations

import textwrap
from pathlib import Path

from hallow.config.loader import HallowConfig
from hallow.core import analyze
from hallow.identity import _fnv1a64, canonical_key, stamp_identities
from hallow.types import Finding, Location, RuleId


def _f(rule: RuleId, file: str, line: int, symbol: str, **meta) -> Finding:
    return Finding(
        rule=rule,
        message="m",
        location=Location(file=file, line=line),
        symbol=symbol,
        metadata=meta,
    )


def test_fnv1a64_known_value():
    # FNV-1a 64 of the empty input is the offset basis; one 0xff byte after one empty part.
    assert _fnv1a64([]) == 0xCBF29CE484222325
    assert _fnv1a64([""]) == ((0xCBF29CE484222325 ^ 0xFF) * 0x100000001B3) & (2**64 - 1)


def test_id_format_and_line_independence():
    a = _f(RuleId.UNUSED_FUNCTIONS, "app/x.py", 10, "helper")
    b = _f(RuleId.UNUSED_FUNCTIONS, "app/x.py", 99, "helper")
    stamp_identities([a])
    stamp_identities([b])
    assert a.finding_id == b.finding_id
    assert a.finding_id.startswith("hl1:unused-functions:")
    assert len(a.finding_id.rsplit(":", 1)[1]) == 16
    assert a.key == "unused-functions:app/x.py:helper"


def test_rename_of_file_or_symbol_changes_id():
    ids = []
    for file, sym in [("x.py", "f"), ("y.py", "f"), ("x.py", "g")]:
        f = _f(RuleId.UNUSED_FUNCTIONS, file, 1, sym)
        stamp_identities([f])
        ids.append(f.finding_id)
    assert len(set(ids)) == 3


def test_repeats_get_occurrence_suffix_in_line_order():
    late = _f(RuleId.UNUSED_IMPORTS, "m.py", 20, "os")
    early = _f(RuleId.UNUSED_IMPORTS, "m.py", 3, "os")
    stamp_identities([late, early])
    assert "~" not in early.finding_id
    assert late.finding_id == early.finding_id + "~1"
    assert late.key == early.key


def test_key_escapes_and_rule_specific_parts():
    assert canonical_key(_f(RuleId.UNUSED_VARIABLES, "a:b.py", 1, "x%")) == (
        "unused-variables:a%3Ab.py:x%25"
    )
    cyc = _f(RuleId.CIRCULAR_DEPENDENCIES, "b.py", 1, "b.py|a.py")
    assert canonical_key(cyc) == "circular-dependencies:a.py|b.py"
    cx = _f(RuleId.HIGH_COMPLEXITY, "m.py", 4, "Cls.run", kind="cognitive")
    assert canonical_key(cx) == "high-complexity:m.py:Cls.run:cognitive"
    # unlisted dependencies are identified by package, wherever the import is
    dep = _f(RuleId.UNLISTED_DEPENDENCIES, "app/main.py", 7, "starlette")
    assert canonical_key(dep) == "unlisted-dependencies:starlette"


def test_path_map_follows_renames():
    f = _f(RuleId.UNUSED_FUNCTIONS, "old.py", 1, "f")
    assert canonical_key(f, {"old.py": "new.py"}) == "unused-functions:new.py:f"


def test_every_finding_from_analysis_is_stamped(tmp_path: Path):
    (tmp_path / "main.py").write_text("import helper\nhelper.run()\n")
    (tmp_path / "helper.py").write_text(
        textwrap.dedent("""\
            import json
            class A:
                def run(self): ...
            class B:
                def run(self): ...
            def run(): ...
        """)
    )
    results = analyze(HallowConfig(root=tmp_path))
    assert results.findings
    assert all(f.finding_id.startswith("hl1:") and f.key for f in results.findings)
    ids = [f.finding_id for f in results.findings]
    assert len(ids) == len(set(ids))
