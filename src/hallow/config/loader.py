"""Config loading, validation, and resolution."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from hallow.types import RuleId, Severity

# ── Rule defaults ──

_DEFAULT_RULES: dict[RuleId, Severity] = {
    RuleId.UNUSED_FILES: Severity.ERROR,
    RuleId.UNUSED_IMPORTS: Severity.ERROR,
    RuleId.UNUSED_FUNCTIONS: Severity.ERROR,
    RuleId.UNUSED_CLASSES: Severity.ERROR,
    RuleId.UNUSED_VARIABLES: Severity.WARN,
    RuleId.UNUSED_DEPENDENCIES: Severity.ERROR,
    RuleId.UNLISTED_DEPENDENCIES: Severity.WARN,
    RuleId.UNUSED_ALL_ENTRIES: Severity.WARN,
    RuleId.CIRCULAR_DEPENDENCIES: Severity.ERROR,
    RuleId.DUPLICATE_CODE: Severity.WARN,
    RuleId.HIGH_COMPLEXITY: Severity.WARN,
    RuleId.BOUNDARY_VIOLATION: Severity.ERROR,
    RuleId.HARDCODED_SECRET: Severity.ERROR,
    RuleId.TAINT_SINK: Severity.OFF,
    RuleId.STALE_SUPPRESSION: Severity.WARN,
}


# ── Config models ──


class RulesConfig(BaseModel):
    overrides: dict[str, Severity] = Field(default_factory=dict)

    def severity_for(self, rule: RuleId) -> Severity:
        if rule.value in self.overrides:
            return self.overrides[rule.value]
        return _DEFAULT_RULES.get(rule, Severity.OFF)


class DuplicatesConfig(BaseModel):
    mode: str = "mild"  # strict, mild, weak, semantic
    min_tokens: int = 50
    min_lines: int = 5
    min_occurrences: int = 2


class HealthConfig(BaseModel):
    max_cyclomatic: int = 20
    max_cognitive: int = 15


class BoundaryZone(BaseModel):
    name: str
    patterns: list[str]


class BoundaryRule(BaseModel):
    from_zone: str = Field(alias="from")
    allow: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class BoundaryConfig(BaseModel):
    preset: str | None = None  # layered, hexagonal, feature-sliced
    zones: list[BoundaryZone] = Field(default_factory=list)
    rules: list[BoundaryRule] = Field(default_factory=list)


class HallowConfig(BaseModel):
    root: Path = Field(default_factory=lambda: Path.cwd())
    entry: list[str] = Field(default_factory=list)
    ignore_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/__pycache__/**",
            "**/.venv/**",
            "**/venv/**",
            "**/node_modules/**",
            "**/.git/**",
            "**/dist/**",
            "**/build/**",
            "**/*.egg-info/**",
        ]
    )
    ignore_dependencies: list[str] = Field(default_factory=list)
    src_paths: list[str] = Field(default_factory=lambda: ["src", "."])
    test_patterns: list[str] = Field(
        default_factory=lambda: ["tests/**", "test/**", "**/test_*.py", "**/*_test.py"]
    )

    rules: RulesConfig = Field(default_factory=RulesConfig)
    duplicates: DuplicatesConfig = Field(default_factory=DuplicatesConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    boundaries: BoundaryConfig = Field(default_factory=BoundaryConfig)

    fail_on_issues: bool = False
    ci: bool = False
    changed_since: str | None = None
    baseline: str | None = None
    format: str = "human"  # human, json, sarif, markdown, compact
    output_file: str | None = None

    model_config = {"arbitrary_types_allowed": True}


def _find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for d in [current, *current.parents]:
        if (d / ".hallowrc.toml").exists():
            return d
        if (d / "pyproject.toml").exists():
            return d
    return current


def _load_hallowrc(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_pyproject_tool_hallow(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("hallow", {})


def _normalize_rules(raw: dict[str, Any]) -> dict[str, Any]:
    if "rules" in raw and isinstance(raw["rules"], dict):
        overrides = {}
        for key, val in raw["rules"].items():
            if isinstance(val, str):
                overrides[key] = val
        raw["rules"] = {"overrides": overrides}
    return raw


def load_config(
    root: Path | None = None,
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> HallowConfig:
    project_root = root or _find_project_root()

    raw: dict[str, Any] = {}

    if config_path:
        raw = _load_hallowrc(config_path)
    else:
        hallowrc = project_root / ".hallowrc.toml"
        pyproject = project_root / "pyproject.toml"
        if hallowrc.exists():
            raw = _load_hallowrc(hallowrc)
        elif pyproject.exists():
            raw = _load_pyproject_tool_hallow(pyproject)

    raw = _normalize_rules(raw)
    raw["root"] = str(project_root)

    if overrides:
        raw.update(overrides)

    return HallowConfig.model_validate(raw)
