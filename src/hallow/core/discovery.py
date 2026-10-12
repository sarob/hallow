"""File discovery — walk filesystem respecting ignore patterns."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from hallow.config.loader import HallowConfig

# A directory holding one of these is a Python environment (venv/virtualenv
# write `pyvenv.cfg`, conda writes `conda-meta/`), whatever the directory is
# called. Its contents are installed third-party code, never project source.
_ENV_MARKERS = ("pyvenv.cfg", "conda-meta")


@lru_cache(maxsize=256)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to a regex.

    `**/` matches zero or more leading directories (so `**/build/**` matches a
    top-level `build/` as well as a nested one), a trailing `**` matches the
    rest of the path, `*` and `?` stay within one path segment.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_glob(rel_path: str, pattern: str) -> bool:
    """True if a root-relative POSIX path matches a glob.

    A pattern without a `/` is also tried against the basename, so `*.pyc` or
    `conftest.py` match at any depth.
    """
    if _glob_to_regex(pattern).match(rel_path):
        return True
    if "/" not in pattern:
        return _glob_to_regex(pattern).match(rel_path.rstrip("/").rsplit("/", 1)[-1]) is not None
    return False


def _should_ignore(rel_path: str, patterns: list[str]) -> bool:
    return any(matches_glob(rel_path, p) for p in patterns)


def _is_env_dir(path: Path) -> bool:
    return any((path / marker).exists() for marker in _ENV_MARKERS)


def discover_python_files(config: HallowConfig) -> list[Path]:
    root = config.root.resolve()
    patterns = config.ignore_patterns
    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else f"{rel_dir}/"

        # Prune in place so ignored trees are never walked. A directory is
        # ignored when a pattern matches it with a trailing slash — which is
        # how `**/build/**` matches a top-level `build/`.
        kept: list[str] = []
        for name in sorted(dirnames):
            rel = f"{prefix}{name}"
            if name.startswith("."):
                continue
            if _should_ignore(f"{rel}/", patterns) or _should_ignore(rel, patterns):
                continue
            if _is_env_dir(current / name):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            if not name.endswith(".py") or name.startswith("."):
                continue
            if _should_ignore(f"{prefix}{name}", patterns):
                continue
            files.append(current / name)

    return sorted(files)
