"""Tests for framework plugins."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hallow.plugins.base import FrameworkPlugin
from hallow.plugins.celery import CeleryPlugin
from hallow.plugins.django import DjangoPlugin
from hallow.plugins.fastapi import FastAPIPlugin
from hallow.plugins.flask import FlaskPlugin
from hallow.plugins.pytest import PytestPlugin
from hallow.plugins.registry import PluginRegistry, load_plugins


def test_django_entry_files():
    plugin = DjangoPlugin()
    assert plugin.is_entry_file("myapp/urls.py")
    assert plugin.is_entry_file("myapp/admin.py")
    assert not plugin.is_entry_file("myapp/utils.py")


def test_django_used_exports():
    plugin = DjangoPlugin()
    assert plugin.is_used_export("urlpatterns", [], "urls.py")
    assert plugin.is_used_export("my_signal", ["receiver"], "signals.py")
    assert not plugin.is_used_export("helper", [], "utils.py")


def test_django_migration_suppression():
    plugin = DjangoPlugin()
    suppressed = plugin.suppressed_rules("myapp/migrations/0001_initial.py")
    assert "unused-imports" in suppressed
    assert "high-complexity" in suppressed


def test_flask_used_exports():
    plugin = FlaskPlugin()
    assert plugin.is_used_export("index", ["route"], "views.py")
    assert plugin.is_used_export("create_app", [], "app.py")
    assert not plugin.is_used_export("helper", [], "utils.py")


def test_fastapi_used_exports():
    plugin = FastAPIPlugin()
    assert plugin.is_used_export("get_users", ["get"], "routes.py")
    assert plugin.is_used_export("app", [], "main.py")


def test_fastapi_used_imports():
    plugin = FastAPIPlugin()
    assert plugin.is_used_import("fastapi", "Depends", "routes.py")
    assert not plugin.is_used_import("fastapi", "FastAPI", "routes.py")


def test_pytest_conftest():
    plugin = PytestPlugin()
    assert plugin.is_entry_file("tests/conftest.py")
    assert plugin.is_used_export("my_fixture", ["fixture"], "conftest.py")
    suppressed = plugin.suppressed_rules("conftest.py")
    assert "unused-functions" in suppressed


def test_celery_used_exports():
    plugin = CeleryPlugin()
    assert plugin.is_used_export("send_email", ["task"], "tasks.py")
    assert plugin.is_used_export("celery_app", [], "celery.py")
    assert plugin.is_entry_file("myapp/tasks.py")


def test_registry_combines_plugins():
    registry = PluginRegistry([DjangoPlugin(), FlaskPlugin()])
    assert registry.is_used_export("urlpatterns", [], "urls.py")
    assert registry.is_used_export("index", ["route"], "views.py")
    assert not registry.is_used_export("helper", [], "utils.py")


def test_load_plugins_from_pyproject():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pyproject = root / "pyproject.toml"
        pyproject.write_text('[project]\ndependencies = ["django>=4.0", "celery>=5.0"]\n')
        registry = load_plugins(root)
        assert any(isinstance(p, DjangoPlugin) for p in registry.plugins)
        assert any(isinstance(p, CeleryPlugin) for p in registry.plugins)
        assert not any(isinstance(p, FlaskPlugin) for p in registry.plugins)


def test_load_plugins_no_pyproject():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        registry = load_plugins(root)
        assert len(registry.plugins) == 0


def test_all_plugins_are_framework_plugins():
    for plugin_cls in [DjangoPlugin, FlaskPlugin, FastAPIPlugin, PytestPlugin, CeleryPlugin]:
        assert issubclass(plugin_cls, FrameworkPlugin)
        assert len(plugin_cls.trigger_packages()) > 0
