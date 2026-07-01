"""Tests for MCP server — JSON-RPC dispatch and tool listing."""

from __future__ import annotations

import json

from hallow.mcp.server import MCPServer, _dispatch_tool, _initialize_result, _tools_list


def test_initialize_result_structure():
    result = _initialize_result()
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"] == {}
    assert result["serverInfo"]["name"] == "hallow"


def test_tools_list_returns_all_tools():
    result = _tools_list()
    names = {t["name"] for t in result["tools"]}
    assert names == {"analyze", "health", "complexity", "cycles", "duplicates", "explain"}


def test_explain_known_rule():
    result = _dispatch_tool("explain", {"rule": "unused-imports"})
    assert "rule" in result
    assert "explanation" in result
    assert result["rule"] == "unused-imports"


def test_explain_unknown_rule():
    result = _dispatch_tool("explain", {"rule": "nonexistent"})
    assert "error" in result
    assert "Unknown rule" in result["error"]


def test_handle_request_tools_list():
    server = MCPServer()
    response = server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response["id"] == 1
    assert "tools" in response["result"]


def test_handle_request_unknown_method():
    server = MCPServer()
    response = server._handle_request({"jsonrpc": "2.0", "id": 2, "method": "bogus/method"})
    assert "error" in response
    assert response["error"]["code"] == -32601


def test_handle_tool_call_explain():
    server = MCPServer()
    response = server._handle_tool_call(
        3, {"name": "explain", "arguments": {"rule": "hardcoded-secret"}}
    )
    assert response["id"] == 3
    content = response["result"]["content"]
    assert len(content) == 1
    parsed = json.loads(content[0]["text"])
    assert parsed["rule"] == "hardcoded-secret"


def test_dispatch_unknown_tool():
    import pytest

    with pytest.raises(ValueError, match="Unknown tool"):
        _dispatch_tool("nonexistent_tool", {})
