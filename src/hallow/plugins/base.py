"""Base class for framework plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod


class FrameworkPlugin(ABC):
    @staticmethod
    @abstractmethod
    def trigger_packages() -> list[str]:
        """Package names that activate this plugin (normalized, lowercase, underscores)."""

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        """Return True if this export should be considered used by the framework."""
        return False

    def is_used_import(self, module: str, name: str, file_path: str) -> bool:
        """Return True if this import should be considered used by the framework."""
        return False

    def is_entry_file(self, file_path: str) -> bool:
        """Return True if this file is a framework entry point (always reachable)."""
        return False

    def suppressed_rules(self, file_path: str) -> set[str]:
        """Return rule IDs that should be suppressed for this file."""
        return set()
