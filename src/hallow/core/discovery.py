"""File discovery — walk filesystem respecting ignore patterns."""

from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path

from hallow.config.loader import HallowConfig

# A directory holding `pyvenv.cfg` (a file, written by venv/virtualenv) or
# `conda-meta/` (a directory, written by conda) is a Python environment,
# whatever it is called. Its contents are installed third-party code.
_ENV_MARKER_FILE = "pyvenv.cfg"
_ENV_MARKER_DIR = "conda-meta"

_FNMATCH_WRAPPER = re.compile(r"\(\?s:(.*)\)\\[zZ]", re.DOTALL)


def _bracket_end(pattern: str, i: int) -> int:
    """Index of the `]` closing the class opened at `i`, or -1 (fnmatch's rules).

    A `]` right after `[` or `[!` is a literal member, not the end.
    """
    j = i + 1
    if j < len(pattern) and pattern[j] == "!":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    end = pattern.find("]", j)
    return end


def _class_regex(cls: str) -> str:
    """Regex for one `[...]` class with exactly fnmatch's semantics.

    fnmatch reads `!` as negation and `^` as a literal member; delegating to
    `fnmatch.translate` keeps that (and its escaping of `\\`, `&&`, `~~`, `||`).
    """
    m = _FNMATCH_WRAPPER.fullmatch(fnmatch.translate(cls))
    return m.group(1) if m else re.escape(cls)


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
            end = _bracket_end(pattern, i)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                out.append(_class_regex(pattern[i : end + 1]))
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
    return (path / _ENV_MARKER_FILE).is_file() or (path / _ENV_MARKER_DIR).is_dir()


def discover_python_files(config: HallowConfig) -> list[Path]:
    """Python files under the root, skipping hidden, ignored and environment directories.

    The root itself is never skipped: pointing `--root` at an environment
    scans it as asked.
    """
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
