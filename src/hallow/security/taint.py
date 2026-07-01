"""Taint sink detection — untrusted input reaching dangerous operations."""

from __future__ import annotations

import ast
from pathlib import Path

from hallow.config.loader import HallowConfig
from hallow.types import Finding, Location, ModuleInfo, RuleId, Severity

_TAINT_SOURCES = frozenset(
    {
        "input",
        "request.args",
        "request.form",
        "request.json",
        "request.data",
        "request.GET",
        "request.POST",
        "request.query_params",
        "sys.argv",
        "os.environ",
    }
)

_DANGEROUS_SINKS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "os.system",
        "os.popen",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "__import__",
        "pickle.loads",
        "yaml.load",
        "yaml.unsafe_load",
        "marshal.loads",
        "shelve.open",
    }
)

_SQL_SINKS = frozenset(
    {
        "execute",
        "executemany",
        "raw",
        "extra",
    }
)


def detect_taint_sinks(
    modules: dict[str, ModuleInfo],
    root: Path,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.TAINT_SINK)
    if severity == Severity.OFF:
        return []

    findings: list[Finding] = []

    for path, module in modules.items():
        if module.is_test or module.is_conftest:
            continue

        full_path = root / path
        if not full_path.exists():
            continue

        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(full_path))
        except (OSError, SyntaxError):
            continue

        _scan_tree(tree, path, severity, findings)

    return findings


def _scan_tree(
    tree: ast.Module,
    path: str,
    severity: Severity,
    findings: list[Finding],
) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func_name = _get_call_name(node)
        if not func_name:
            continue

        if func_name in _DANGEROUS_SINKS and _has_dynamic_arg(node):
            findings.append(
                Finding(
                    rule=RuleId.TAINT_SINK,
                    severity=severity,
                    message=f"Dangerous function '{func_name}' called with dynamic argument",
                    location=Location(file=path, line=node.lineno, col=node.col_offset),
                    suggestion=f"Avoid passing user-controlled data to '{func_name}'",
                    metadata={"sink": func_name, "kind": "dangerous_call"},
                )
            )

        if func_name.split(".")[-1] in _SQL_SINKS and _has_fstring_or_format_arg(node):
            findings.append(
                Finding(
                    rule=RuleId.TAINT_SINK,
                    severity=severity,
                    message=f"Potential SQL injection via '{func_name}' with string formatting",
                    location=Location(file=path, line=node.lineno, col=node.col_offset),
                    suggestion="Use parameterized queries instead of string formatting",
                    metadata={"sink": func_name, "kind": "sql_injection"},
                )
            )


def _get_call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts = []
        obj = node.func
        while isinstance(obj, ast.Attribute):
            parts.append(obj.attr)
            obj = obj.value
        if isinstance(obj, ast.Name):
            parts.append(obj.id)
        return ".".join(reversed(parts))
    return None


def _has_dynamic_arg(node: ast.Call) -> bool:
    return any(not isinstance(arg, ast.Constant) for arg in node.args)


def _has_fstring_or_format_arg(node: ast.Call) -> bool:
    for arg in node.args:
        if isinstance(arg, ast.JoinedStr):
            return True
        if isinstance(arg, ast.Call):
            name = _get_call_name(arg)
            if name and name.endswith(".format"):
                return True
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
            return True
    return False
