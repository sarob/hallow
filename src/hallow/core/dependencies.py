"""Declared dependencies — read from pyproject.toml, or requirements files as a fallback."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# PEP 508 distribution name at the start of a requirement string.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


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
    lines = text.splitlines()
    project = data.get("project", {})
    result: list[DeclaredDependency] = []

    def add(spec: object, optional: bool) -> None:
        if not isinstance(spec, str):
            return  # `{include-group = ...}` entries in dependency-groups
        name = requirement_name(spec)
        if name:
            result.append(
                DeclaredDependency(
                    name=name,
                    file="pyproject.toml",
                    line=_line_of(lines, spec),
                    optional=optional,
                )
            )

    for spec in project.get("dependencies", []):
        add(spec, optional=False)
    for group in project.get("optional-dependencies", {}).values():
        for spec in group:
            add(spec, optional=True)
    for group in data.get("dependency-groups", {}).values():
        for spec in group:
            add(spec, optional=True)
    return result


def _line_of(lines: list[str], spec: str) -> int:
    for quote in ('"', "'"):
        needle = f"{quote}{spec}{quote}"
        for i, line in enumerate(lines, 1):
            if needle in line:
                return i
    return 1


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
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            include = line.split(None, 1)[1].strip()
            _read_requirements_file(path.parent / include, root, optional, result, seen)
            continue
        if line.startswith("-"):
            continue  # -e, -c, --index-url and other options
        if "://" in line.split("@", 1)[0] or line.startswith((".", "/")):
            continue  # bare URLs and local paths carry no distribution name
        name = requirement_name(line)
        if name:
            result.append(DeclaredDependency(name=name, file=rel, line=i, optional=optional))
