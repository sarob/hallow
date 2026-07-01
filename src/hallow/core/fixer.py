"""Auto-fix engine — apply safe fixes for findings."""

from __future__ import annotations

import re
from pathlib import Path

from hallow.types import AnalysisResults, Finding, RuleId


class FixResult:
    def __init__(self) -> None:
        self.removed_imports: list[str] = []
        self.deleted_files: list[str] = []
        self.removed_deps: list[str] = []
        self.errors: list[str] = []

    @property
    def total(self) -> int:
        return len(self.removed_imports) + len(self.deleted_files) + len(self.removed_deps)


def apply_fixes(
    results: AnalysisResults,
    root: Path,
    dry_run: bool = True,
) -> FixResult:
    fix_result = FixResult()

    import_findings = [f for f in results.findings if f.rule == RuleId.UNUSED_IMPORTS and f.fix]
    _fix_unused_imports(import_findings, root, dry_run, fix_result)

    file_findings = [f for f in results.findings if f.rule == RuleId.UNUSED_FILES and f.fix]
    _fix_unused_files(file_findings, root, dry_run, fix_result)

    dep_findings = [f for f in results.findings if f.rule == RuleId.UNUSED_DEPENDENCIES and f.fix]
    _fix_unused_deps(dep_findings, root, dry_run, fix_result)

    return fix_result


def _fix_unused_imports(
    findings: list[Finding],
    root: Path,
    dry_run: bool,
    result: FixResult,
) -> None:
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.location.file, []).append(f)

    for file_path, file_findings in by_file.items():
        full_path = root / file_path
        if not full_path.exists():
            continue

        try:
            source = full_path.read_text(encoding="utf-8")
        except OSError:
            continue

        lines = source.splitlines(keepends=True)
        lines_to_remove: set[int] = set()

        for finding in file_findings:
            if not finding.fix:
                continue
            target = finding.fix.target
            line_idx = finding.location.line - 1

            if line_idx < 0 or line_idx >= len(lines):
                continue

            line = lines[line_idx]

            if _can_remove_name_from_import(line, target):
                new_line = _remove_name_from_import(line, target)
                if new_line is None:
                    lines_to_remove.add(line_idx)
                else:
                    lines[line_idx] = new_line
                result.removed_imports.append(f"{file_path}: {target}")
            elif f"import {target}" in line:
                lines_to_remove.add(line_idx)
                result.removed_imports.append(f"{file_path}: {target}")

        if not dry_run and (lines_to_remove or result.removed_imports):
            new_source = "".join(line for i, line in enumerate(lines) if i not in lines_to_remove)
            full_path.write_text(new_source, encoding="utf-8")


def _can_remove_name_from_import(line: str, name: str) -> bool:
    return "import" in line and name in line


def _remove_name_from_import(line: str, name: str) -> str | None:
    stripped = line.strip()

    match = re.match(r"^from\s+\S+\s+import\s+(.+)$", stripped)
    if not match:
        if re.match(rf"^import\s+{re.escape(name)}\s*$", stripped):
            return None
        return line

    names_part = match.group(1).strip()
    if names_part.startswith("("):
        names_part = names_part.strip("()")
    names = [n.strip() for n in names_part.split(",") if n.strip()]

    filtered = [n for n in names if n != name and not n.startswith(f"{name} as ")]
    if not filtered:
        return None

    prefix = line[: line.index("import") + 6]
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}{prefix.strip()} {', '.join(filtered)}\n"


def _fix_unused_files(
    findings: list[Finding],
    root: Path,
    dry_run: bool,
    result: FixResult,
) -> None:
    for finding in findings:
        if not finding.fix:
            continue
        file_path = root / finding.fix.target
        if file_path.exists():
            result.deleted_files.append(finding.fix.target)
            if not dry_run:
                file_path.unlink()


def _fix_unused_deps(
    findings: list[Finding],
    root: Path,
    dry_run: bool,
    result: FixResult,
) -> None:
    if not findings:
        return

    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return

    dep_names = {f.fix.target for f in findings if f.fix}
    if not dep_names:
        return

    try:
        source = pyproject.read_text(encoding="utf-8")
    except OSError:
        return

    lines = source.splitlines(keepends=True)
    new_lines: list[str] = []
    in_deps = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("dependencies") and "=" in stripped:
            in_deps = True
            new_lines.append(line)
            continue

        if in_deps:
            if stripped == "]":
                in_deps = False
                new_lines.append(line)
                continue
            if stripped.startswith("[") and not stripped.startswith('"'):
                in_deps = False
                new_lines.append(line)
                continue

            dep_line_name = _extract_dep_name(stripped)
            if dep_line_name and dep_line_name in dep_names:
                result.removed_deps.append(dep_line_name)
                continue

        new_lines.append(line)

    if not dry_run and result.removed_deps:
        pyproject.write_text("".join(new_lines), encoding="utf-8")


def _extract_dep_name(line: str) -> str | None:
    cleaned = line.strip().strip('"').strip("'").strip(",")
    if not cleaned:
        return None
    name = cleaned.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
    if name:
        return name.lower().replace("-", "_").replace(".", "_")
    return None
