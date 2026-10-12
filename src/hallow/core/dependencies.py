"""Declared dependencies — read from pyproject.toml, or requirements files as a fallback."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# PEP 508 distribution name at the start of a requirement string.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
# `-r FILE`, `-rFILE`, `--requirement FILE`, `--requirement=FILE`
_INCLUDE_RE = re.compile(r"^(?:-r\s*|--requirement(?:\s+|\s*=\s*))(?P<path>\S+)")
_ARCHIVE_SUFFIXES = (".whl", ".tar.gz", ".tgz", ".zip", ".tar.bz2", ".tar.xz")


@dataclass(frozen=True)
class DeclaredDependency:
    name: str  # normalized: lowercase, runs of -_. collapsed to "_"
    file: str  # root-relative file that declares it
    line: int
    optional: bool  # optional-dependencies, dependency-groups, or dev requirements files


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def requirement_name(spec: str) -> str | None:
    """Distribution name of a PEP 508 requirement string, normalized."""
    m = _NAME_RE.match(spec)
    return normalize_name(m.group(1)) if m else None


def dependency_manifest(root: Path) -> str | None:
    """The file dependencies are declared in, or None when the project has none."""
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            if "project" in tomllib.loads(pyproject.read_text(encoding="utf-8")):
                return "pyproject.toml"
        except (OSError, tomllib.TOMLDecodeError):
            pass
    if (root / "requirements.txt").exists():
        return "requirements.txt"
    return None


def read_declared_dependencies(root: Path) -> list[DeclaredDependency]:
    """Dependencies the project declares.

    `pyproject.toml` `[project]` is authoritative when present: `dependencies`
    are runtime, `optional-dependencies` and PEP 735 `[dependency-groups]` are
    optional. Without a `[project]` table, `requirements.txt` is read as
    runtime and any other `requirements*.txt` or `requirements/*.txt` as
    optional, following `-r` includes.
    """
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            data = tomllib.loads(text)
        except (OSError, tomllib.TOMLDecodeError):
            data, text = {}, ""
        if "project" in data:
            return _from_pyproject(data, text)

    return _from_requirements(root)


def _from_pyproject(data: dict, text: str) -> list[DeclaredDependency]:
    from hallow.core.toml_locations import array_string_lines

    located = array_string_lines(text)
    project = data.get("project", {})
    result: list[DeclaredDependency] = []

    def add(spec: object, optional: bool, path: tuple[str, ...]) -> None:
        if not isinstance(spec, str):
            return  # `{include-group = ...}` entries in dependency-groups
        name = requirement_name(spec)
        if not name:
            return
        # Each parsed entry takes the next located element with the same
        # decoded value in the same array, so positions follow the array,
        # never free text elsewhere in the file.
        entries = located.get(path, [])
        index = next((i for i, (_, v) in enumerate(entries) if v == spec), None)
        line = entries.pop(index)[0] if index is not None else 1
        result.append(
            DeclaredDependency(name=name, file="pyproject.toml", line=line, optional=optional)
        )

    for spec in project.get("dependencies", []):
        add(spec, False, ("project", "dependencies"))
    for group, specs in project.get("optional-dependencies", {}).items():
        for spec in specs:
            add(spec, True, ("project", "optional-dependencies", group))
    for group, specs in data.get("dependency-groups", {}).items():
        for spec in specs:
            add(spec, True, ("dependency-groups", group))
    return result


def _from_requirements(root: Path) -> list[DeclaredDependency]:
    runtime = root / "requirements.txt"
    optional_files = sorted(
        {p for p in root.glob("requirements*.txt") if p != runtime}
        | set(root.glob("*-requirements.txt"))
        | set((root / "requirements").glob("*.txt"))
    )

    result: list[DeclaredDependency] = []
    seen: set[Path] = set()
    if runtime.exists():
        _read_requirements_file(runtime, root, False, result, seen)
    for path in optional_files:
        _read_requirements_file(path, root, True, result, seen)
    return result


def logical_requirement_lines(text: str):
    """Yield (first line, last line, text) per logical line of a requirements file.

    Backslash continuations are joined, so `pkg==1 \\` followed by an
    indented `--hash=...` line is one requirement spanning both lines.
    Comments are stripped.
    """
    start = 0
    parts: list[str] = []
    lines = text.splitlines()
    for i, raw in enumerate(lines, 1):
        if not parts:
            start = i
        line = raw.split(" #", 1)[0] if not raw.lstrip().startswith("#") else ""
        if line.rstrip().endswith("\\"):
            parts.append(line.rstrip()[:-1])
            if i < len(lines):
                continue
        else:
            parts.append(line)
        yield start, i, " ".join(p.strip() for p in parts).strip()
        parts = []


def requirement_line_name(line: str) -> str | None:
    """The distribution a requirements-file line installs, or None.

    None for options (`-e`, `-c`, `--index-url`, ...), includes, URLs, local
    paths (`./pkg`, `vendor/pkg`, `C:\\pkg`) and archive files (`x.whl`).
    `name @ url` direct references keep their name.
    """
    if not line or line.startswith("-"):
        return None
    head = line.split(";", 1)[0].strip()
    if " @ " in head or re.match(r"^[A-Za-z0-9._-]+(\[[^\]]*\])?\s*@", head):
        return requirement_name(head)
    if "/" in head or "\\" in head or head.startswith("."):
        return None
    first = head.split()[0] if head.split() else ""
    if first.lower().endswith(_ARCHIVE_SUFFIXES):
        return None
    return requirement_name(head)


def _read_requirements_file(
    path: Path,
    root: Path,
    optional: bool,
    result: list[DeclaredDependency],
    seen: set[Path],
) -> None:
    resolved = path.resolve()
    if resolved in seen or not path.exists():
        return
    seen.add(resolved)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return

    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    for first, _last, line in logical_requirement_lines(text):
        include = _INCLUDE_RE.match(line)
        if include:
            _read_requirements_file(
                path.parent / include.group("path"), root, optional, result, seen
            )
            continue
        name = requirement_line_name(line)
        if name:
            result.append(DeclaredDependency(name=name, file=rel, line=first, optional=optional))
