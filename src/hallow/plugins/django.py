"""Django framework plugin."""

from __future__ import annotations

from hallow.plugins.base import FrameworkPlugin

_DJANGO_ENTRY_PATTERNS = (
    "urls.py",
    "admin.py",
    "apps.py",
    "signals.py",
    "templatetags/",
    "management/commands/",
    "migrations/",
    "wsgi.py",
    "asgi.py",
)

_DJANGO_DECORATORS = frozenset(
    {
        "receiver",
        "register",
        "admin_register",
        "login_required",
        "permission_required",
        "api_view",
        "action",
    }
)


class DjangoPlugin(FrameworkPlugin):
    @staticmethod
    def trigger_packages() -> list[str]:
        return ["django"]

    def is_used_export(self, name: str, decorators: list[str], file_path: str) -> bool:
        if any(d in _DJANGO_DECORATORS for d in decorators):
            return True
        if any(file_path.endswith(p) or f"/{p}" in file_path for p in _DJANGO_ENTRY_PATTERNS):
            return True
        return name in ("urlpatterns", "default_app_config", "app_name")

    def is_used_import(self, module: str, name: str, file_path: str) -> bool:
        return file_path.endswith("models.py") and name in ("Model", "Manager", "QuerySet")

    def is_entry_file(self, file_path: str) -> bool:
        return any(file_path.endswith(p) or f"/{p}" in file_path for p in _DJANGO_ENTRY_PATTERNS)

    def suppressed_rules(self, file_path: str) -> set[str]:
        if "migrations/" in file_path:
            return {"unused-imports", "unused-functions", "unused-classes", "high-complexity"}
        return set()
