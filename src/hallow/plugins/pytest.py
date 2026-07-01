"""Pytest framework plugin."""

from __future__ import annotations

from hallow.plugins.base import FrameworkPlugin

_PYTEST_DECORATORS = frozenset(
    {
        "fixture",
        "mark",
        "parametrize",
        "hookimpl",
        "hookspec",
    }
)


class PytestPlugin(FrameworkPlugin):
    @staticmethod
    def trigger_packages() -> list[str]:
        return ["pytest"]

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        if any(d in _PYTEST_DECORATORS for d in decorators):
            return True
        return "conftest.py" in file_path

    def is_entry_file(self, file_path: str) -> bool:
        return "conftest.py" in file_path

    def suppressed_rules(self, file_path: str) -> set[str]:
        if "conftest.py" in file_path:
            return {"unused-functions", "unused-imports"}
        return set()
