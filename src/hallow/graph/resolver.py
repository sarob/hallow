"""Resolve import strings to file paths."""

from __future__ import annotations

from pathlib import Path

from hallow.types import ImportInfo


def _module_to_candidates(module: str) -> list[str]:
    parts = module.split(".")
    base = "/".join(parts)
    return [
        f"{base}.py",
        f"{base}/__init__.py",
    ]


def resolve_import(
    imp: ImportInfo,
    importer_path: str,
    root: Path,
    all_paths: set[str],
) -> str | None:
    if imp.is_relative:
        return _resolve_relative(imp, importer_path, root, all_paths)
    return _resolve_absolute(imp.module, root, all_paths)


def resolve_submodule(
    imp: ImportInfo,
    importer_path: str,
    name: str,
    all_paths: set[str],
) -> str | None:
    """Resolve `from <pkg> import <name>` to a submodule file, if one exists.

    `from fulcrum_cli.commands import bootstrap` imports the *module*
    `fulcrum_cli/commands/bootstrap.py`, not a symbol of the package. Returns the
    submodule path when it exists in the project, else None (so ordinary symbol
    imports like `from typing import List` never create spurious edges).
    """
    if not name or name == "*":
        return None

    prefixes: list[str] = []
    if imp.is_relative:
        base_dir = Path(importer_path).parent
        for _ in range(imp.level - 1):
            base_dir = base_dir.parent
        if imp.module:
            base_dir = base_dir / "/".join(imp.module.split("."))
        prefixes.append(base_dir.as_posix())
    else:
        if not imp.module:
            return None
        pkg = "/".join(imp.module.split("."))
        prefixes.extend([pkg, f"src/{pkg}"])

    for prefix in prefixes:
        base = name if prefix in (".", "") else f"{prefix}/{name}"
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if candidate in all_paths:
                return candidate
    return None


def _resolve_relative(
    imp: ImportInfo,
    importer_path: str,
    root: Path,
    all_paths: set[str],
) -> str | None:
    importer = Path(importer_path)
    base_dir = importer.parent

    for _ in range(imp.level - 1):
        base_dir = base_dir.parent

    if imp.module:
        parts = imp.module.split(".")
        target_dir = base_dir / "/".join(parts)
        candidates = [
            str(target_dir) + ".py",
            str(target_dir / "__init__.py"),
        ]
    else:
        candidates = [str(base_dir / "__init__.py")]
        for name in imp.names:
            candidates.append(str(base_dir / f"{name}.py"))
            candidates.append(str(base_dir / name / "__init__.py"))

    for candidate in candidates:
        if candidate in all_paths:
            return candidate

    return None


def _resolve_absolute(
    module: str,
    root: Path,
    all_paths: set[str],
) -> str | None:
    if not module:
        return None

    candidates = _module_to_candidates(module)
    for candidate in candidates:
        if candidate in all_paths:
            return candidate

    parts = module.split(".")
    for src_prefix in ["src/", ""]:
        base = src_prefix + "/".join(parts)
        for suffix in [".py", "/__init__.py"]:
            candidate = base + suffix
            if candidate in all_paths:
                return candidate

    if len(parts) > 1:
        parent_module = ".".join(parts[:-1])
        return _resolve_absolute(parent_module, root, all_paths)

    return None
