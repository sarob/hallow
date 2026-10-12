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

        labels = _enum_label_positions(source)
        for line_num, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue

            hit = _first_secret(line, line_num, labels)
            if hit is None:
                continue
            label, match = hit
            key = _assigned_key(line, match)
            findings.append(
                Finding(
                    rule=RuleId.HARDCODED_SECRET,
                    severity=severity,
                    message=f"Potential {label} detected",
                    location=Location(file=path, line=line_num, col=match.start()),
                    symbol=f"{label}:{key}" if key else label,
                    suggestion="Move secrets to environment variables or a secrets manager",
                    metadata={"kind": label},
                )
            )

    return findings


def _first_secret(
    line: str, line_num: int, labels: set[tuple[int, int]]
) -> tuple[str, re.Match[str]] | None:
    """The first reportable match on a line (one finding per line).

    Later matches are still examined after an exempt enum label, so a label
    does not hide a real secret further along the same line.
    """
    for label, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(line):
            if _is_safe_context(line, match.group()):
                break
            value_start = match.start("value") if "value" in match.groupdict() else -1
            # the opening quote of an exempt label literal sits at (line, col)
            if value_start > 0 and (line_num, value_start - 1) in labels:
                continue
            return label, match
    return None


_ENUM_TYPES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"})


def _enum_label_positions(source: str) -> set[tuple[int, int]]:
    """(line, column) of each enum label literal: a member whose value spells its name.

    Only exact members count: `NAME = "name"` (a single name target and a
    plain string value) in the body of a class whose base resolves to the
    `enum` module, through `from enum import X [as Y]`, `import enum [as e]`,
    or another such class in the same file. Anything unresolved, nested in an
    expression, or with a different value stays reportable.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    enum_names: set[str] = set()  # local names bound to enum base types
    enum_modules: set[str] = set()  # local names bound to the enum module
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "enum" and node.level == 0:
            for alias in node.names:
                if alias.name in _ENUM_TYPES:
                    enum_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "enum":
                    enum_modules.add(alias.asname or "enum")

    def is_enum_base(base: ast.expr) -> bool:
        if isinstance(base, ast.Name):
            return base.id in enum_names
        return (
            isinstance(base, ast.Attribute)
            and isinstance(base.value, ast.Name)
            and base.value.id in enum_modules
            and base.attr in _ENUM_TYPES
        )

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    enum_classes: list[ast.ClassDef] = []
    changed = True
    while changed:  # classes deriving from enum classes defined in this file
        changed = False
        for cls in classes:
            if cls not in enum_classes and any(is_enum_base(b) for b in cls.bases):
                enum_classes.append(cls)
                enum_names.add(cls.name)
                changed = True

    positions: set[tuple[int, int]] = set()
    for cls in enum_classes:
        for stmt in cls.body:
            if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
                continue
            target, value = stmt.targets[0], stmt.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _norm(value.value) == _norm(target.id)
            ):
                # col_offset counts UTF-8 bytes; it equals the str index for
                # ASCII lines. A mismatch only makes the label reportable.
                positions.add((value.lineno, value.col_offset))
    return positions


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _assigned_key(line: str, match: re.Match[str]) -> str:
    """The full identifier being assigned, e.g. `HARDCODED_SECRET` for a match on `SECRET`."""
    sep = re.search(r"[=:]", line[match.start() :])
    end = match.start() + sep.start() if sep else match.start()
    m = _KEY_BEFORE_RE.search(line[:end])
    return m.group(1) if m else ""


def _is_safe_context(line: str, matched: str) -> bool:
    return any(safe in line for safe in _SAFE_PATTERNS)
