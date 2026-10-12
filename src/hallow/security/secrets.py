"""Hardcoded secret detection — API keys, tokens, passwords, private keys."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from hallow.config.loader import HallowConfig
from hallow.types import Finding, Location, ModuleInfo, RuleId, Severity

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "AWS secret key",
        re.compile(
            r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key"
            r"\s*[=:]\s*['\"][A-Za-z0-9/+=]{40}['\"]"
        ),
    ),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9a-zA-Z\-]{10,}")),
    ("Stripe key", re.compile(r"[sr]k_(live|test)_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    (
        "Generic secret",
        re.compile(
            r"(?i)(?:password|passwd|secret|api[_\-]?key|auth[_\-]?token)"
            r"\s*[=:]\s*['\"](?P<value>[^'\"]{8,})['\"]"
        ),
    ),
    (
        "Hex token (32+)",
        re.compile(r"(?i)(?:token|key|secret)\s*[=:]\s*['\"](?P<value>[0-9a-f]{32,})['\"]"),
    ),
]

# The identifier left of the `=`/`:` a pattern matched inside.
_KEY_BEFORE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_.\-]*)['\"]?\s*$")

_SAFE_PATTERNS = frozenset(
    {
        "os.environ",
        "os.getenv",
        "environ.get",
        "environ[",
        "settings.",
        "config.",
        "getattr(",
        "${",
        "{{",
        "<PLACEHOLDER",
        "CHANGE_ME",
        "YOUR_",
        "xxx",
        "***",
        "...",
    }
)


def detect_hardcoded_secrets(
    modules: dict[str, ModuleInfo],
    root: Path,
    config: HallowConfig,
) -> list[Finding]:
    severity = config.rules.severity_for(RuleId.HARDCODED_SECRET)
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
        except OSError:
            continue

        enum_lines = _enum_member_lines(source)
        for line_num, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue

            hit = _first_secret(line, in_enum=line_num in enum_lines)
            if hit is None:
                continue
            label, match = hit
            findings.append(
                Finding(
                    rule=RuleId.HARDCODED_SECRET,
                    severity=severity,
                    message=f"Potential {label} detected",
                    location=Location(file=path, line=line_num, col=match.start()),
                    suggestion="Move secrets to environment variables or a secrets manager",
                    metadata={"kind": label},
                )
            )

    return findings


def _first_secret(line: str, in_enum: bool) -> tuple[str, re.Match[str]] | None:
    """The first reportable match on a line (one finding per line).

    Every match of every pattern is considered, so a skipped enum label does
    not hide a real secret later on the same line.
    """
    for label, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(line):
            if _is_safe_context(line, match.group()):
                break
            if in_enum and _value_names_key(match, _assigned_key(line, match)):
                continue
            return label, match
    return None


_ENUM_BASES = ("Enum", "Flag")


def _enum_member_lines(source: str) -> set[int]:
    """Lines holding a member assignment in the body of an Enum/Flag subclass."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases]
        if not any(name.endswith(_ENUM_BASES) for name in bases):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign | ast.AnnAssign):
                lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    return lines


def _assigned_key(line: str, match: re.Match[str]) -> str:
    """The full identifier being assigned, e.g. `HARDCODED_SECRET` for a match on `SECRET`."""
    sep = re.search(r"[=:]", line[match.start() :])
    end = match.start() + sep.start() if sep else match.start()
    m = _KEY_BEFORE_RE.search(line[:end])
    return m.group(1) if m else ""


def _value_names_key(match: re.Match[str], key: str) -> bool:
    """True when the value only spells out the key's own name.

    Applied only to Enum/Flag members, where `HARDCODED_SECRET =
    "hardcoded-secret"` is a label. Anywhere else, a password whose value
    equals its own name is still a credential and stays reported.
    """
    value = match.groupdict().get("value")
    if not value or not key:
        return False

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    return norm(value) == norm(key.rsplit(".", 1)[-1])


def _is_safe_context(line: str, matched: str) -> bool:
    return any(safe in line for safe in _SAFE_PATTERNS)
