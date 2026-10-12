"""Tests for security detectors — secrets and taint sinks."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.config.loader import HallowConfig, RulesConfig
from hallow.security.secrets import detect_hardcoded_secrets
from hallow.security.taint import detect_taint_sinks
from hallow.types import ModuleInfo, RuleId, Severity


def _modules_from_source(source: str, path: str = "app.py") -> tuple[dict[str, ModuleInfo], Path]:
    tmpdir = TemporaryDirectory()
    root = Path(tmpdir.name)
    (root / path).write_text(source)
    modules = {
        path: ModuleInfo(path=path, line_count=len(source.splitlines())),
    }
    return modules, root, tmpdir


def _config(secret_sev: Severity = Severity.ERROR, taint_sev: Severity = Severity.OFF):
    overrides = {
        RuleId.HARDCODED_SECRET.value: secret_sev,
        RuleId.TAINT_SINK.value: taint_sev,
    }
    return HallowConfig(rules=RulesConfig(overrides=overrides))


# ── Secret Detection ──


def test_detects_aws_access_key():
    modules, root, tmp = _modules_from_source('KEY = "AKIAIOSFODNN7EXAMPLE"')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 1
        assert findings[0].rule == RuleId.HARDCODED_SECRET
        assert "AWS access key" in findings[0].message
    finally:
        tmp.cleanup()


def test_detects_github_token():
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl"
    modules, root, tmp = _modules_from_source(f'TOKEN = "{token}"')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 1
        assert "GitHub token" in findings[0].message
    finally:
        tmp.cleanup()


def test_safe_context_skips_env_var():
    modules, root, tmp = _modules_from_source('KEY = os.environ["AKIAIOSFODNN7EXAMPLE"]')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_safe_context_skips_getenv():
    modules, root, tmp = _modules_from_source('KEY = os.getenv("AKIAIOSFODNN7EXAMPLE")')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_skips_comments():
    modules, root, tmp = _modules_from_source('# KEY = "AKIAIOSFODNN7EXAMPLE"')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_skips_test_files():
    modules, root, tmp = _modules_from_source('KEY = "AKIAIOSFODNN7EXAMPLE"', path="test_app.py")
    modules["test_app.py"].is_test = True
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_respects_severity_off():
    modules, root, tmp = _modules_from_source('KEY = "AKIAIOSFODNN7EXAMPLE"')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config(secret_sev=Severity.OFF))
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_detects_generic_secret():
    modules, root, tmp = _modules_from_source('password = "supersecretpassword123"')
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 1
        assert "Generic secret" in findings[0].message
    finally:
        tmp.cleanup()


# ── Taint Sink Detection ──


def test_detects_eval_with_dynamic_arg():
    source = """\
x = input()
result = eval(x)
"""
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.ERROR))
        assert len(findings) == 1
        assert findings[0].rule == RuleId.TAINT_SINK
        assert "eval" in findings[0].message
    finally:
        tmp.cleanup()


def test_skips_eval_with_literal():
    source = 'result = eval("1 + 1")\n'
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.ERROR))
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_detects_os_system():
    source = """\
import os
os.system(cmd)
"""
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.ERROR))
        assert len(findings) == 1
        assert "os.system" in findings[0].message
    finally:
        tmp.cleanup()


def test_detects_sql_injection_fstring():
    source = """\
cursor.execute(f"SELECT * FROM users WHERE id={user_id}")
"""
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.ERROR))
        assert len(findings) == 1
        assert "SQL injection" in findings[0].message
    finally:
        tmp.cleanup()


def test_taint_respects_severity_off():
    source = "result = eval(x)\n"
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.OFF))
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_taint_skips_test_files():
    source = "result = eval(x)\n"
    modules, root, tmp = _modules_from_source(source, path="test_thing.py")
    modules["test_thing.py"].is_test = True
    try:
        findings = detect_taint_sinks(modules, root, _config(taint_sev=Severity.ERROR))
        assert len(findings) == 0
    finally:
        tmp.cleanup()


def test_enum_member_spelling_its_name_is_not_a_secret():
    # Regression: `HARDCODED_SECRET = "hardcoded-secret"` (a StrEnum member) was an error.
    source = (
        "from enum import StrEnum\n"
        "class RuleId(StrEnum):\n"
        '    HARDCODED_SECRET = "hardcoded-secret"\n'
        '    API_KEY = "api_key_value"\n'
    )
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert [f.location.line for f in findings] == [4]
    finally:
        tmp.cleanup()


def test_password_equal_to_its_name_outside_an_enum_is_reported():
    # Review of #12: name equality alone does not make a value a label.
    source = 'password = "password"\nclass Cfg:\n    PASSWORD = "P@a$s$s!w-o_r.d"\n'
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert [f.location.line for f in findings] == [1, 3]
    finally:
        tmp.cleanup()


def test_skipped_label_does_not_hide_a_secret_on_the_same_line():
    # Review of #12: the exempt first match must not end the search.
    source = (
        "import enum\n"
        "class K(enum.Enum):\n"
        '    HARDCODED_SECRET = "hardcoded-secret"; password = "sensitive123!"\n'
    )
    modules, root, tmp = _modules_from_source(source)
    try:
        findings = detect_hardcoded_secrets(modules, root, _config())
        assert len(findings) == 1
        assert findings[0].location.col == source.splitlines()[2].index("password")
    finally:
        tmp.cleanup()


# ── review of #12, round 2 ──


def _lines_found(source: str) -> list[int]:
    modules, root, tmp = _modules_from_source(source)
    try:
        return [f.location.line for f in detect_hardcoded_secrets(modules, root, _config())]
    finally:
        tmp.cleanup()


def test_unrelated_base_ending_in_flag_or_enum_is_not_an_enum():
    source = (
        "class FeatureFlag:\n"
        "    pass\n"
        "class MyEnum:\n"
        "    pass\n"
        "class Credentials(FeatureFlag):\n"
        '    PASSWORD = "password"\n'
        "class More(MyEnum):\n"
        '    PASSWORD = "password"\n'
    )
    assert _lines_found(source) == [6, 8]


def test_nested_credential_inside_an_enum_member_is_reported():
    source = (
        "from enum import Enum\n"
        "class Settings(Enum):\n"
        '    LOGIN = dict(password="password")\n'
        "    OTHER = dict(\n"
        '        password="password",\n'
        "    )\n"
    )
    assert _lines_found(source) == [3, 5]


def test_enum_bases_resolve_through_aliases_and_local_subclasses():
    source = (
        "import enum as e\n"
        "from enum import StrEnum as S\n"
        "class A(S):\n"
        '    HARDCODED_SECRET = "hardcoded-secret"\n'
        "class B(e.Flag):\n"
        '    AUTH_TOKEN = "auth_token"\n'
        "class Base(e.Enum):\n"
        "    pass\n"
        "class C(Base):\n"
        '    AUTH_TOKEN = "auth-token"\n'
    )
    assert _lines_found(source) == []
    # control: the same literal outside an enum is reported
    assert _lines_found('AUTH_TOKEN = "auth-token"\n') == [1]


def test_rebound_enum_alias_is_not_trusted():
    # Review of #21, round 2: `Base = object` makes Credentials an ordinary class.
    source = (
        "from enum import Enum as Base\n"
        "Base = object\n"
        "class Credentials(Base):\n"
        '    PASSWORD = "password"\n'
        "import enum as e\n"
        "def e(): ...\n"
        "class More(e.Enum):\n"
        '    PASSWORD = "password"\n'
    )
    # each PASSWORD would be an exempt label if its alias were trusted
    assert _lines_found(source) == [4, 8]


def test_parameter_shadowing_an_enum_import_is_not_trusted():
    # Review of #12, round 3.
    source = (
        "from enum import Enum as Base\n"
        "def make_credentials(Base):\n"
        "    class Credentials(Base):\n"
        '        PASSWORD = "password"\n'
        "    return Credentials\n"
        "import enum as e\n"
        "f = lambda e: e\n"
        "class More(e.Enum):\n"
        '    PASSWORD = "password"\n'
    )
    # both would be exempt labels if the aliases were trusted: the parameter
    # shadows `Base` (line 4), the lambda parameter shadows `e` (line 9)
    assert _lines_found(source) == [4, 9]
    # control: without the lambda, `e.Enum` is trusted and the PASSWORD on
    # line 3 of this source is an exempt label
    trusted = 'import enum as e\nclass More(e.Enum):\n    PASSWORD = "password"\n'
    assert _lines_found(trusted) == []


def test_label_position_uses_characters_not_utf8_bytes():
    # Review of #12, round 3: 16 two-byte characters before `X = "x"` made its
    # byte column equal the password's character column.
    source = (
        "from enum import Enum\n"
        "class Settings(Enum):\n"
        "    " + "\u00e9" * 16 + ' = 0; X = "x"; password = "sensitive123!"\n'
    )
    assert _lines_found(source) == [3]
