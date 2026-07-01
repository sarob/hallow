"""SARIF v2.1.0 output — Static Analysis Results Interchange Format."""

from __future__ import annotations

import json
from typing import Any

from hallow import __version__
from hallow.types import AnalysisResults, Finding, Severity


def format_sarif(results: AnalysisResults, file: Any = None) -> str:
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [_build_run(results)],
    }
    text = json.dumps(sarif, indent=2)
    if file:
        file.write(text)
    return text


def _build_run(results: AnalysisResults) -> dict:
    rules_seen: dict[str, int] = {}
    rules: list[dict] = []
    sarif_results: list[dict] = []

    for finding in results.findings:
        rule_idx = _ensure_rule(finding, rules_seen, rules)
        sarif_results.append(_finding_to_result(finding, rule_idx))

    for cycle in results.cycles:
        rule_id = "circular-dependencies"
        if rule_id not in rules_seen:
            rules_seen[rule_id] = len(rules)
            rules.append(
                {
                    "id": rule_id,
                    "shortDescription": {"text": "Circular import dependency"},
                    "defaultConfiguration": {"level": "error"},
                }
            )
        chain = " -> ".join(cycle.modules + [cycle.modules[0]])
        sarif_results.append(
            {
                "ruleId": rule_id,
                "ruleIndex": rules_seen[rule_id],
                "level": "error",
                "message": {"text": f"Circular import: {chain}"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": cycle.modules[0]},
                            "region": {"startLine": 1},
                        }
                    }
                ],
            }
        )

    return {
        "tool": {
            "driver": {
                "name": "hallow",
                "version": __version__,
                "informationUri": "https://github.com/sarob/hallow",
                "rules": rules,
            }
        },
        "results": sarif_results,
    }


def _ensure_rule(finding: Finding, seen: dict[str, int], rules: list[dict]) -> int:
    rule_id = finding.rule.value
    if rule_id in seen:
        return seen[rule_id]
    idx = len(rules)
    seen[rule_id] = idx
    rules.append(
        {
            "id": rule_id,
            "shortDescription": {"text": _rule_description(rule_id)},
            "defaultConfiguration": {"level": _severity_to_level(finding.severity)},
        }
    )
    return idx


def _finding_to_result(finding: Finding, rule_index: int) -> dict:
    result: dict[str, Any] = {
        "ruleId": finding.rule.value,
        "ruleIndex": rule_index,
        "level": _severity_to_level(finding.severity),
        "message": {"text": finding.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": finding.location.file},
                    "region": {"startLine": max(finding.location.line, 1)},
                }
            }
        ],
    }
    if finding.suggestion:
        result["fixes"] = [
            {
                "description": {"text": finding.suggestion},
            }
        ]
    return result


def _severity_to_level(severity: Severity) -> str:
    return {
        Severity.ERROR: "error",
        Severity.WARN: "warning",
        Severity.OFF: "note",
    }[severity]


def _rule_description(rule_id: str) -> str:
    descriptions = {
        "unused-files": "File is not imported by any module",
        "unused-imports": "Imported name is not used in the module",
        "unused-functions": "Function is defined but never imported",
        "unused-classes": "Class is defined but never imported",
        "unused-variables": "Variable is defined but never imported",
        "unused-dependencies": "Dependency declared but never imported",
        "unlisted-dependencies": "Import not declared in dependencies",
        "unused-all-entries": "Name in __all__ not defined in module",
        "circular-dependencies": "Circular import dependency",
        "duplicate-code": "Duplicate code block detected",
        "high-complexity": "Function exceeds complexity threshold",
        "boundary-violation": "Import violates architecture boundary",
        "hardcoded-secret": "Potential hardcoded secret detected",
        "taint-sink": "Untrusted input reaches dangerous sink",
        "stale-suppression": "Suppression comment no longer needed",
    }
    return descriptions.get(rule_id, rule_id)
