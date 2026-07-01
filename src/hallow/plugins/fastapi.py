"""FastAPI framework plugin."""

from __future__ import annotations

from hallow.plugins.base import FrameworkPlugin

_FASTAPI_DECORATORS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "trace",
        "websocket",
        "on_event",
        "middleware",
        "exception_handler",
    }
)


class FastAPIPlugin(FrameworkPlugin):
    @staticmethod
    def trigger_packages() -> list[str]:
        return ["fastapi"]

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        if any(d in _FASTAPI_DECORATORS for d in decorators):
            return True
        return name in ("app", "router")

    def is_used_import(self, module: str, name: str, file_path: str) -> bool:
        return name in ("Depends", "Query", "Path", "Body", "Header", "Cookie", "Form", "File")

    def is_entry_file(self, file_path: str) -> bool:
        return file_path.endswith("main.py") or file_path.endswith("app.py")
