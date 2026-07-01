"""Plugin registry — discovers frameworks from pyproject.toml and provides suppression rules."""

from __future__ import annotations

import tomllib
from pathlib import Path

from hallow.plugins.base import FrameworkPlugin
from hallow.plugins.celery import CeleryPlugin
from hallow.plugins.django import DjangoPlugin
from hallow.plugins.fastapi import FastAPIPlugin
from hallow.plugins.flask import FlaskPlugin
from hallow.plugins.pytest import PytestPlugin

_ALL_PLUGINS: list[type[FrameworkPlugin]] = [
    DjangoPlugin,
    FlaskPlugin,
    FastAPIPlugin,
    PytestPlugin,
    CeleryPlugin,
]


class PluginRegistry:
    def __init__(self, plugins: list[FrameworkPlugin] | None = None) -> None:
        self.plugins: list[FrameworkPlugin] = plugins or []

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        return any(p.is_used_export(name, decorators, file_path) for p in self.plugins)

    def is_used_import(self, module: str, name: str, file_path: str) -> bool:
        return any(p.is_used_import(module, name, file_path) for p in self.plugins)

    def is_entry_file(self, file_path: str) -> bool:
        return any(p.is_entry_file(file_path) for p in self.plugins)

    def suppressed_rules(self, file_path: str) -> set[str]:
        result: set[str] = set()
        for p in self.plugins:
            result.update(p.suppressed_rules(file_path))
        return result


def load_plugins(root: Path) -> PluginRegistry:
    deps = _read_dependencies(root)
    active: list[FrameworkPlugin] = []

    for plugin_cls in _ALL_PLUGINS:
        for trigger in plugin_cls.trigger_packages():
            if trigger in deps:
                active.append(plugin_cls())
                break

    return PluginRegistry(active)


def _read_dependencies(root: Path) -> set[str]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return set()

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return set()

    deps: set[str] = set()
    project = data.get("project", {})

    for dep in project.get("dependencies", []):
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
        if name:
            deps.add(name.lower().replace("-", "_"))

    for group_deps in project.get("optional-dependencies", {}).values():
        for dep in group_deps:
            name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].split(";")[0].strip()
            if name:
                deps.add(name.lower().replace("-", "_"))

    return deps
