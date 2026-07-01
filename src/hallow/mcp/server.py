"""MCP server implementation — stdio transport, JSON-RPC 2.0."""

from __future__ import annotations

import json
import sys
from typing import Any

from hallow import __version__
from hallow.api import (
    compute_complexity,
    compute_health,
    detect_circular_dependencies,
    detect_dead_code,
    find_duplicates,
)

_TOOLS: dict[str, dict[str, Any]] = {
    "analyze": {
        "description": "Run full hallow analysis on a Python project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Project root directory"},
                "format": {
                    "type": "string",
                    "enum": ["json", "sarif", "markdown", "codeclimate"],
                    "default": "json",
                },
            },
        },
    },
    "health": {
        "description": "Compute project health score (0-100, grade A-F)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Project root directory"},
            },
        },
    },
    "complexity": {
        "description": "List per-function complexity metrics",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Project root directory"},
                "threshold": {
                    "type": "integer",
                    "description": "Only show functions with cyclomatic >= threshold",
                    "default": 1,
                },
            },
        },
    },
    "cycles": {
        "description": "Detect circular import dependencies",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Project root directory"},
            },
        },
    },
    "duplicates": {
        "description": "Find duplicate code blocks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Project root directory"},
                "mode": {
                    "type": "string",
                    "enum": ["strict", "mild", "weak", "semantic"],
                    "default": "mild",
                },
            },
        },
    },
    "explain": {
        "description": "Explain what a specific hallow rule checks for",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule": {"type": "string", "description": "Rule ID (e.g. unused-imports)"},
            },
            "required": ["rule"],
        },
    },
}

_RULE_EXPLANATIONS = {
    "unused-files": (
        "Detects Python files not imported by any other module. "
        "Uses BFS reachability from entry points."
    ),
    "unused-imports": (
        "Finds imported names never used in the module's scope. "
        "Skips TYPE_CHECKING imports and __init__.py re-exports."
    ),
    "unused-functions": "Functions defined at module level but never imported.",
    "unused-classes": "Classes defined at module level but never imported.",
    "unused-variables": "Module-level variables never imported by another module.",
    "unused-dependencies": (
        "Packages declared in pyproject.toml [project.dependencies] "
        "but never imported anywhere in the codebase."
    ),
    "unlisted-dependencies": (
        "Packages imported in code but not declared in pyproject.toml. Filters out stdlib modules."
    ),
    "circular-dependencies": (
        "Detects import cycles using Tarjan's strongly connected components algorithm."
    ),
    "duplicate-code": (
        "Finds cloned code blocks via tokenization and rolling hash. "
        "Supports strict, mild, weak, and semantic modes."
    ),
    "high-complexity": (
        "Flags functions exceeding cyclomatic or cognitive complexity "
        "thresholds. Default: cyclomatic > 20, cognitive > 15."
    ),
    "boundary-violation": (
        "Enforces architecture zones (layered, hexagonal, feature-sliced). "
        "Flags imports that cross zone boundaries."
    ),
    "hardcoded-secret": (
        "Detects potential API keys, tokens, passwords, and private keys embedded in source code."
    ),
    "taint-sink": (
        "Flags dangerous function calls (eval, exec, os.system, SQL execute) "
        "with dynamic arguments that may carry user input."
    ),
}


def create_mcp_app():
    return MCPServer()


class MCPServer:
    def run_stdio(self) -> None:
        self._write_response(_initialize_response())

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue

            response = self._handle_request(request)
            if response:
                self._write_response(response)

    def _handle_request(self, request: dict) -> dict | None:
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return _make_response(req_id, _initialize_result())
        if method == "tools/list":
            return _make_response(req_id, _tools_list())
        if method == "tools/call":
            return self._handle_tool_call(req_id, request.get("params", {}))
        if method == "shutdown":
            return _make_response(req_id, None)

        return _make_error(req_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, req_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            result = _dispatch_tool(tool_name, arguments)
            return _make_response(
                req_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                },
            )
        except Exception as e:
            return _make_response(
                req_id,
                {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            )

    def _write_response(self, response: dict) -> None:
        text = json.dumps(response)
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def _dispatch_tool(name: str, args: dict) -> Any:
    root = args.get("root")

    if name == "analyze":
        fmt = args.get("format", "json")
        results = detect_dead_code(root=root)
        if fmt == "json":
            return results.model_dump(mode="json")
        from hallow.output import format_results

        return format_results(results, fmt=fmt)

    if name == "health":
        health = compute_health(root=root)
        return health.model_dump(mode="json")

    if name == "complexity":
        threshold = args.get("threshold", 1)
        funcs = compute_complexity(root=root)
        return [f for f in funcs if f["cyclomatic"] >= threshold]

    if name == "cycles":
        return detect_circular_dependencies(root=root)

    if name == "duplicates":
        mode = args.get("mode", "mild")
        return find_duplicates(root=root, mode=mode)

    if name == "explain":
        rule = args.get("rule", "")
        explanation = _RULE_EXPLANATIONS.get(rule)
        if explanation:
            return {"rule": rule, "explanation": explanation}
        return {"rule": rule, "error": f"Unknown rule: {rule}"}

    raise ValueError(f"Unknown tool: {name}")


def _initialize_response() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "result": _initialize_result(),
    }


def _initialize_result() -> dict:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": "hallow",
            "version": __version__,
        },
    }


def _tools_list() -> dict:
    tools = []
    for name, spec in _TOOLS.items():
        tools.append(
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
        )
    return {"tools": tools}


def _make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
