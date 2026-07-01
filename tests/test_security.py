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
