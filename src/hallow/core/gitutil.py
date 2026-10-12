"""Git plumbing for change-scoped runs: base resolution, changed files, worktrees."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from hallow.exit_codes import HallowError

BASE_ENV = "HALLOW_AUDIT_BASE"

# Tried in order when no base is given (after fallow's audit base detection).
_AUTO_BASES = ("@{upstream}", "origin/HEAD", "origin/main", "origin/master", "main", "master")

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise HallowError("git is not installed", code="HALLOW_GIT_MISSING") from e
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "no output"
        raise HallowError(f"git {args[0]} failed: {detail}", code="HALLOW_GIT_FAILED")
    return proc.stdout


def _ok(cwd: Path, *args: str) -> str | None:
    try:
        return git(cwd, *args).strip()
    except HallowError:
        return None


def repo_top(root: Path) -> Path:
    out = _ok(root, "rev-parse", "--show-toplevel")
    if not out:
        raise HallowError(f"{root} is not inside a git repository", code="HALLOW_NOT_A_REPO")
    return Path(out).resolve()


@dataclass
class BaseRef:
    ref: str  # what the user (or detection) named
    sha: str  # merge-base of ref and HEAD: the commit the change is compared against
    description: str | None = None  # set when detected, e.g. "merge-base with origin/main"


def resolve_base(top: Path, explicit: str | None) -> BaseRef:
    """The base commit: `--base`, then $HALLOW_AUDIT_BASE, then detection.

    Detection tries the branch's upstream, then origin/HEAD, origin/main,
    origin/master, main, master, and uses the merge-base with HEAD.
    """
    given = explicit or os.environ.get(BASE_ENV) or None
    if given:
        # A leading `-` would be read by git as an option, not a revision.
        commit = (
            None
            if given.startswith("-")
            else _ok(top, "rev-parse", "--verify", "--quiet", f"{given}^{{commit}}")
        )
        sha = _ok(top, "merge-base", commit, "HEAD") if commit else None
        if not sha:
            raise HallowError(f"cannot resolve base '{given}' against HEAD", code="HALLOW_BAD_BASE")
        return BaseRef(ref=given, sha=sha)

    for candidate in _AUTO_BASES:
        sha = _ok(top, "merge-base", candidate, "HEAD")
        if sha:
            name = _ok(top, "rev-parse", "--abbrev-ref", candidate) or candidate
            return BaseRef(ref=name, sha=sha, description=f"merge-base with {name}")
    raise HallowError(
        "could not detect a base branch",
        code="HALLOW_NO_BASE",
        help="Pass --base <ref> or set HALLOW_AUDIT_BASE.",
    )


@dataclass
class ChangeSet:
    """Changes between a base commit and the working tree, root-relative."""

    files: set[str] = field(default_factory=set)  # added, modified, renamed (new name)
    renames: dict[str, str] = field(default_factory=dict)  # old path -> new path
    added_lines: dict[str, set[int]] = field(default_factory=dict)
    untracked: set[str] = field(default_factory=set)


def _to_root(path: str, prefix: str) -> str | None:
    if not prefix:
        return path
    return path[len(prefix) :] if path.startswith(prefix) else None


def collect_changes(top: Path, root: Path, base_sha: str) -> ChangeSet:
    """Committed, staged, unstaged and untracked changes since `base_sha`.

    Paths are made relative to `root` (which may be a subdirectory of the
    repository); changes outside it are dropped.
    """
    rel = root.relative_to(top).as_posix()
    prefix = "" if rel == "." else f"{rel}/"
    cs = ChangeSet()

    out = git(top, "diff", "--name-status", "-z", "--find-renames", base_sha, "--")
    parts = [p for p in out.split("\0") if p]
    i = 0
    while i < len(parts):
        status = parts[i]
        if status.startswith(("R", "C")):
            old, new = parts[i + 1], parts[i + 2]
            i += 3
            new_r = _to_root(new, prefix)
            old_r = _to_root(old, prefix)
            if new_r is not None:
                cs.files.add(new_r)
                if old_r is not None and status.startswith("R"):
                    cs.renames[old_r] = new_r
            continue
        path = parts[i + 1]
        i += 2
        if status.startswith("D"):
            continue
        p = _to_root(path, prefix)
        if p is not None:
            cs.files.add(p)

    for path in git(top, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        p = _to_root(path, prefix) if path else None
        if p is not None:
            cs.files.add(p)
            cs.untracked.add(p)

    # Added lines per changed Python file, one `git diff` per file under a
    # literal pathspec: file names never have to be parsed back out of patch
    # headers, which git quotes (non-ASCII, tabs, quotes) or re-prefixes
    # (diff.noprefix, diff.mnemonicPrefix).
    old_for = {new: old for old, new in cs.renames.items()}
    for p in sorted(cs.files - cs.untracked):
        if not p.endswith(".py"):
            continue
        specs = [f":(literal){prefix}{p}"]
        if p in old_for:
            specs.insert(0, f":(literal){prefix}{old_for[p]}")
        patch = git(
            top, "-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--no-ext-diff",
            "--find-renames", base_sha, "--", *specs,
        )  # fmt: skip
        for line in patch.splitlines():
            m = _HUNK_RE.match(line)
            if m:
                start_line, count = int(m.group(1)), int(m.group(2) or "1")
                cs.added_lines.setdefault(p, set()).update(range(start_line, start_line + count))
    for p in cs.untracked:
        try:
            n = len((root / p).read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        cs.added_lines[p] = set(range(1, n + 1))
    return cs


@contextmanager
def worktree(top: Path, sha: str) -> Iterator[Path]:
    """A temporary detached checkout of `sha`, removed afterwards."""
    tmp = Path(tempfile.mkdtemp(prefix="hallow-base-"))
    path = tmp / "base"
    try:
        # inside the try: a failed `worktree add` still removes the temp dir
        # and prunes any half-registered worktree
        git(top, "worktree", "add", "--detach", "--quiet", str(path), sha)
        yield path
    finally:
        _ok(top, "worktree", "remove", "--force", str(path))
        shutil.rmtree(tmp, ignore_errors=True)
        _ok(top, "worktree", "prune")


def show_file(top: Path, sha: str, path: str) -> str | None:
    """A file's content at a commit, or None when it does not exist there."""
    return _ok(top, "show", f"{sha}:{path}")
