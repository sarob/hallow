"""Hardcoded secret detection — API keys, tokens, passwords, private keys."""

from __future__ import annotations

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
            r"\s*[=:]\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
    ("Hex token (32+)", re.compile(r"(?i)(?:token|key|secret)\s*[=:]\s*['\"][0-9a-f]{32,}['\"]")),
]

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

        for line_num, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue

            for label, pattern in _SECRET_PATTERNS:
                match = pattern.search(line)
                if match and not _is_safe_context(line, match.group()):
                    findings.append(
                        Finding(
                            rule=RuleId.HARDCODED_SECRET,
                            severity=severity,
                            message=f"Potential {label} detected",
                            location=Location(
                                file=path,
                                line=line_num,
                                col=match.start(),
                            ),
                            suggestion="Move secrets to environment variables or a secrets manager",
                            metadata={"kind": label},
                        )
                    )
                    break

    return findings


def _is_safe_context(line: str, matched: str) -> bool:
    return any(safe in line for safe in _SAFE_PATTERNS)
