"""Allowed values of CLI options, shared by the CLI and the `hallow schema` manifest.

Kept apart from cli.main so the manifest can read them without importing the
CLI module that imports the manifest.
"""

from __future__ import annotations

CHECK_FORMATS = ("human", "json", "compact", "sarif", "markdown", "codeclimate", "github")
DOC_FORMATS = ("human", "json")
DUPE_MODES = ("strict", "mild", "weak", "semantic")

# Allowed values of string options that are validated in the command body
# rather than by the parser; `hallow schema` publishes them per command.
FLAG_VALUES: dict[tuple[str, str], tuple[str, ...]] = {
    **{(c, "--format"): CHECK_FORMATS for c in ("", "check", "audit", "security")},
    **{(c, "--format"): DOC_FORMATS for c in ("health", "dupes", "baselines prune")},
    ("dupes", "--mode"): DUPE_MODES,
    ("", "--boundaries"): ("layered", "hexagonal", "feature-sliced"),
    ("check", "--boundaries"): ("layered", "hexagonal", "feature-sliced"),
}
