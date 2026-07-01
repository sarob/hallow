"""CodeClimate JSON output — for GitLab Code Quality and CodeClimate integrations."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from hallow.types import AnalysisResults, Finding, Severity


def format_codeclimate(results: AnalysisResults, file: Any = None) -> str:
    issues: list[dict] = []

    for finding in results.findings:
        issues.append(_finding_to_issue(finding))

    for cycle in results.cycles:
        chain = " -> ".join(cycle.modules + [cycle.modules[0]])
        issues.append(
            {
                "type": "issue",
                "check_name": "circular-dependencies",
                "description": f"Circular import: {chain}",
                "severity": "critical",
                "fingerprint": _fingerprint(f"circular:{chain}"),
                "location": {
                    "path": cycle.modules[0],
                    "lines": {"begin": 1},
                },
                "categories": ["Complexity"],
            }
        )

    text = json.dumps(issues, indent=2)
    if file:
        file.write(text)
    return text


def _finding_to_issue(finding: Finding) -> dict:
    return {
        "type": "issue",
        "check_name": finding.rule.value,
        "description": finding.message,
        "severity": _severity_to_cc(finding.severity),
        "fingerprint": _fingerprint(
            f"{finding.rule.value}:{finding.location.file}:{finding.location.line}:{finding.message}"
        ),
        "location": {
            "path": finding.location.file,
            "lines": {"begin": max(finding.location.line, 1)},
        },
        "categories": [_rule_category(finding.rule.value)],
    }


def _severity_to_cc(severity: Severity) -> str:
    return {
        Severity.ERROR: "critical",
        Severity.WARN: "minor",
        Severity.OFF: "info",
    }[severity]


def _rule_category(rule_id: str) -> str:
    categories = {
        "unused-files": "Clarity",
        "unused-imports": "Clarity",
        "unused-functions": "Clarity",
        "unused-classes": "Clarity",
        "unused-variables": "Clarity",
        "unused-dependencies": "Clarity",
        "unlisted-dependencies": "Bug Risk",
        "circular-dependencies": "Complexity",
        "duplicate-code": "Duplication",
        "high-complexity": "Complexity",
        "boundary-violation": "Style",
        "hardcoded-secret": "Security",
        "taint-sink": "Security",
    }
    return categories.get(rule_id, "Clarity")


def _fingerprint(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()  # noqa: S324
