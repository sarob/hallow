"""Flask framework plugin."""

from __future__ import annotations

from hallow.plugins.base import FrameworkPlugin

_FLASK_DECORATORS = frozenset(
    {
        "route",
        "before_request",
        "after_request",
        "teardown_request",
        "errorhandler",
        "before_app_request",
        "after_app_request",
        "cli",
        "command",
    }
)


class FlaskPlugin(FrameworkPlugin):
    @staticmethod
    def trigger_packages() -> list[str]:
        return ["flask"]

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        if any(d in _FLASK_DECORATORS for d in decorators):
            return True
        return name in ("create_app", "app")

    def is_entry_file(self, file_path: str) -> bool:
        return file_path.endswith("app.py") or file_path.endswith("wsgi.py")
