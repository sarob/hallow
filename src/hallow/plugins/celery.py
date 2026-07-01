"""Celery framework plugin."""

from __future__ import annotations

from hallow.plugins.base import FrameworkPlugin

_CELERY_DECORATORS = frozenset(
    {
        "task",
        "shared_task",
        "periodic_task",
        "on_after_configure",
        "on_after_finalize",
    }
)


class CeleryPlugin(FrameworkPlugin):
    @staticmethod
    def trigger_packages() -> list[str]:
        return ["celery"]

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        if any(d in _CELERY_DECORATORS for d in decorators):
            return True
        return name in ("app", "celery_app")

    def is_entry_file(self, file_path: str) -> bool:
        return file_path.endswith("celery.py") or file_path.endswith("tasks.py")
