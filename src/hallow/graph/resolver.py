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
