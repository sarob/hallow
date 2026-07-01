"""Analysis orchestration — detectors and pipeline."""

from hallow.core.analyzer import analyze
from hallow.core.discovery import discover_python_files

__all__ = ["analyze", "discover_python_files"]
