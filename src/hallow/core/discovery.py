"""File discovery — walk filesystem respecting ignore patterns."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from hallow.config.loader import HallowConfig


def _should_ignore(path: Path, root: Path, patterns: list[str]) -> bool:
    rel = str(path.relative_to(root))
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
        if fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts if part != ".")


def discover_python_files(config: HallowConfig) -> list[Path]:
    root = config.root.resolve()
    files: list[Path] = []

    for py_file in root.rglob("*.py"):
        if _is_hidden(py_file.relative_to(root)):
            continue
        if _should_ignore(py_file, root, config.ignore_patterns):
            continue
        files.append(py_file)

    return sorted(files)
