"""`hallow schema` — a JSON description of the CLI for scripts and agents.

Built from the live command definitions, so it cannot drift from the CLI
(after fallow's `fallow schema` manifest).
"""

from __future__ import annotations

from typing import Any

import typer

from hallow import __version__
from hallow.cli.options import FLAG_VALUES
from hallow.config.loader import _DEFAULT_RULES
from hallow.core.boundaries import _PRESETS
from hallow.exit_codes import EXIT_CODES
from hallow.output.sarif import _rule_description

MANIFEST_VERSION = "1"

_SKIP_PARAMS = {"version", "install_completion", "show_completion", "help"}


# Typer 0.2x vendors click privately, so commands and options are read by
# duck typing rather than isinstance checks against click classes.


def _flag(param: Any) -> dict[str, Any]:
    longs = [o for o in param.opts if o.startswith("--")]
    shorts = [o for o in param.opts if not o.startswith("--")]
    item: dict[str, Any] = {
        "name": longs[0] if longs else param.opts[0],
        "type": "bool" if param.is_flag else _type_name(param.type),
        "required": bool(param.required),
        "description": param.help or "",
    }
    if param.secondary_opts:
        item["negation"] = param.secondary_opts[0]
    if shorts:
        item["short"] = shorts[0]
    default = param.default
    if default is not None and not callable(default) and not param.required:
        item["default"] = default if isinstance(default, bool | int | str) else str(default)
    choices = getattr(param.type, "choices", None)
    if choices:
        item["possible_values"] = list(choices)
    return item


def _type_name(t: Any) -> str:
    name = getattr(t, "name", "")
    return {"integer": "integer", "path": "path", "float": "number"}.get(name, "string")


def _flags(command: Any, name: str = "") -> list[dict[str, Any]]:
    out = []
    for p in command.params:
        if getattr(p, "param_type_name", "") != "option" or p.name in _SKIP_PARAMS:
            continue
        item = _flag(p)
        values = FLAG_VALUES.get((name, item["name"]))
        if values and "possible_values" not in item:
            item["possible_values"] = list(values)
        out.append(item)
    return out


def _commands(group: Any, prefix: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in sorted(group.commands):
        cmd = group.commands[name]
        full = f"{prefix}{name}"
        if hasattr(cmd, "commands"):
            result.extend(_commands(cmd, f"{full} "))
            continue
        description = (cmd.help or "").strip().split("\n", 1)[0]
        result.append({"name": full, "description": description, "flags": _flags(cmd, full)})
    return result


def build_manifest(app: typer.Typer) -> dict[str, Any]:
    from hallow.mcp.server import _TOOLS

    root = typer.main.get_command(app)
    return {
        "name": "hallow",
        "version": __version__,
        "manifest_version": MANIFEST_VERSION,
        "description": "Deterministic codebase intelligence for Python.",
        "default_command": "check",
        "global_flags": _flags(root),
        "commands": _commands(root),
        "issue_types": [
            {
                "id": rule.value,
                "default_severity": severity.value,
                "description": _rule_description(rule.value),
            }
            for rule, severity in _DEFAULT_RULES.items()
        ],
        "severity_levels": ["error", "warn", "off"],
        "suppression_comments": {
            "line": "# hallow-ignore [rule[, rule...]] [-- reason]",
            "next-line": "# hallow-ignore-next-line [rule[, rule...]] [-- reason]",
            "file": "# hallow-ignore-file [rule[, rule...]] [-- reason]",
        },
        "output_formats": [
            "human",
            "json",
            "compact",
            "sarif",
            "markdown",
            "codeclimate",
            "github",
        ],
        "exit_codes": {str(code): text for code, text in EXIT_CODES.items()},
        "boundary_presets": sorted(_PRESETS),
        "mcp_tools": sorted(_TOOLS),
        "related_schemas": {"output": "hallow schema --output-schema (docs/output-schema.json)"},
    }
