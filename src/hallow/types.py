"""Shared data structures for Hallow. No logic — pure schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ── Severity & Rules ──


class Severity(StrEnum):
    ERROR = "error"
    WARN = "warn"
    OFF = "off"


class RuleId(StrEnum):
    UNUSED_FILES = "unused-files"
    UNUSED_IMPORTS = "unused-imports"
    UNUSED_FUNCTIONS = "unused-functions"
    UNUSED_CLASSES = "unused-classes"
    UNUSED_VARIABLES = "unused-variables"
    UNUSED_DEPENDENCIES = "unused-dependencies"
    UNLISTED_DEPENDENCIES = "unlisted-dependencies"
    UNUSED_ALL_ENTRIES = "unused-all-entries"
    CIRCULAR_DEPENDENCIES = "circular-dependencies"
    DUPLICATE_CODE = "duplicate-code"
    HIGH_COMPLEXITY = "high-complexity"
    BOUNDARY_VIOLATION = "boundary-violation"
    HARDCODED_SECRET = "hardcoded-secret"
    TAINT_SINK = "taint-sink"
    STALE_SUPPRESSION = "stale-suppression"


# ── Extraction Types ──


class ImportInfo(BaseModel):
    module: str
    names: list[str] = Field(default_factory=list)  # source names (for graph resolution)
    bound_names: list[str] = Field(default_factory=list)  # locally-bound names (after `as`)
    alias: str | None = None
    is_from_import: bool = False
    is_relative: bool = False
    level: int = 0
    is_type_checking: bool = False
    is_conditional: bool = False
    is_try_except: bool = False
    line: int = 0
    col: int = 0


class ExportInfo(BaseModel):
    name: str
    kind: str  # "function", "class", "variable", "constant", "type_alias"
    line: int = 0
    col: int = 0
    decorators: list[str] = Field(default_factory=list)
    is_dunder: bool = False
    is_private: bool = False


class FunctionComplexity(BaseModel):
    name: str
    kind: str  # "function", "method", "classmethod", "staticmethod", "property"
    line: int = 0
    end_line: int = 0
    cyclomatic: int = 1
    cognitive: int = 0
    parameters: int = 0
    lines_of_code: int = 0


class ModuleInfo(BaseModel):
    path: str
    package: str = ""
    imports: list[ImportInfo] = Field(default_factory=list)
    exports: list[ExportInfo] = Field(default_factory=list)
    all_list: list[str] | None = None
    functions: list[FunctionComplexity] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    global_variables: list[str] = Field(default_factory=list)
    # names used (loaded) anywhere in the module body
    referenced_names: set[str] = Field(default_factory=set)
    # physical lines carrying a `# noqa` that suppresses unused-imports (F401)
    noqa_lines: set[int] = Field(default_factory=set)
    docstring: str | None = None
    is_init: bool = False
    is_main: bool = False
    is_test: bool = False
    is_conftest: bool = False
    line_count: int = 0
    content_hash: str = ""


# ── Finding Types ──


class Location(BaseModel):
    file: str
    line: int = 0
    col: int = 0
    end_line: int | None = None
    end_col: int | None = None


class FixAction(BaseModel):
    kind: str  # "remove_import", "delete_file", "remove_dependency", "remove_from_all"
    target: str  # what to remove (import name, file path, dep name)
    auto_fixable: bool = True


class Finding(BaseModel):
    rule: RuleId
    severity: Severity = Severity.ERROR
    message: str
    location: Location
    suggestion: str | None = None
    fix: FixAction | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Cycle Types ──


class ImportCycle(BaseModel):
    modules: list[str]
    edges: list[tuple[str, str]] = Field(default_factory=list)


# ── Duplication Types ──


class DuplicateFragment(BaseModel):
    file: str
    start_line: int
    end_line: int
    lines_of_code: int = 0


class DuplicateGroup(BaseModel):
    fragments: list[DuplicateFragment]
    token_count: int = 0
    line_count: int = 0


# ── Health Types ──


class FileHealth(BaseModel):
    path: str
    cyclomatic_avg: float = 0.0
    cyclomatic_max: int = 0
    cognitive_avg: float = 0.0
    cognitive_max: int = 0
    maintainability_index: float = 100.0
    lines_of_code: int = 0
    hotspot_functions: list[str] = Field(default_factory=list)


class ProjectHealth(BaseModel):
    score: int = 100  # 0-100
    grade: str = "A"  # A, B, C, D, F
    total_files: int = 0
    total_functions: int = 0
    total_lines: int = 0
    cyclomatic_avg: float = 0.0
    cognitive_avg: float = 0.0
    maintainability_avg: float = 0.0
    hotspots: list[FileHealth] = Field(default_factory=list)


# ── Analysis Results ──


class AnalysisResults(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    cycles: list[ImportCycle] = Field(default_factory=list)
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    health: ProjectHealth | None = None

    total_files_scanned: int = 0
    total_issues: int = 0
    errors: int = 0
    warnings: int = 0

    def compute_totals(self) -> None:
        self.total_issues = len(self.findings) + len(self.cycles) + len(self.duplicates)
        self.errors = sum(1 for f in self.findings if f.severity == Severity.ERROR)
        self.warnings = sum(1 for f in self.findings if f.severity == Severity.WARN)

    def merge(self, other: AnalysisResults) -> None:
        self.findings.extend(other.findings)
        self.cycles.extend(other.cycles)
        self.duplicates.extend(other.duplicates)
        self.total_files_scanned += other.total_files_scanned
        self.compute_totals()

    def sort(self) -> None:
        self.findings.sort(key=lambda f: (f.location.file, f.location.line, f.rule.value))
        self.cycles.sort(key=lambda c: len(c.modules))
        self.duplicates.sort(key=lambda d: -d.token_count)
