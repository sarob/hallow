"""End-to-end tests: every CLI command and output format on one sample project.

These assert behaviour that holds across releases (exit codes 0/1 for a
completed run, findings present in each format, fixes applied), not output
layout.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hallow import api
from hallow.config.loader import HallowConfig
from hallow.core import analyze
from hallow.mcp.server import MCPServer, _dispatch_tool

runner = CliRunner()

_BRANCHY = "def tangled(x):\n" + "".join(
    f"    if x == {i}:\n        return {i}\n" for i in range(25)
)
_CLONE = "\n".join(f"    total_{i} = compute(value_{i}) + offset_{i}" for i in range(14))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    files = {
        "pyproject.toml": """
            [project]
            name = "sample"
            dependencies = [
                "requests>=2",
                "leftpad",
            ]
        """,
        "main.py": """
            import json
            import requests
            import yaml
            from app import service
            from app.models import user

            service.run(requests, yaml, user)
        """,
        "app/__init__.py": "",
        "app/service.py": """
            import os

            from app.models import user

            UNUSED_LIMIT = 10


            class Unused:
                pass


            def run(*args):
                return user.load(args)


            def helper_unused():
                return 1


            def first():
        """,
        "app/models/__init__.py": "",
        "app/models/user.py": """
            from app import service

            password = "hunter2hunter2"


            def load(args):
                return service


            def second():
        """,
        # hallow treats a module nothing imports as an entry point; an unused
        # file is one reachable only from another unreachable file
        "island/__init__.py": "",
        "island/a.py": "from island import b\n",
        "island/b.py": "from island import a\n",
    }
    # generated blocks are appended after dedenting, so indentation stays valid
    tails = {"app/service.py": _CLONE + "\n", "app/models/user.py": _CLONE + "\n\n" + _BRANCHY}
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n") + tails.get(rel, ""))
    return tmp_path


def _rules(project: Path) -> set[str]:
    cfg = HallowConfig(root=project)
    cfg.duplicates.min_tokens = 30
    return {f.rule.value for f in analyze(cfg).findings}


def test_analysis_finds_each_kind_of_issue(project: Path):
    rules = _rules(project)
    expected = {
        "unused-files",
        "unused-imports",
        "unused-functions",
        "unused-classes",
        "unused-variables",
        "unused-dependencies",
        "unlisted-dependencies",
        "circular-dependencies",
        "high-complexity",
        "hardcoded-secret",
    }
    assert expected <= rules, expected - rules


@pytest.mark.parametrize(
    "fmt", ["human", "json", "compact", "sarif", "markdown", "codeclimate", "github"]
)
def test_check_every_format(project: Path, fmt: str):
    result = runner.invoke(app_cli(), ["check", "--root", str(project), "-f", fmt])
    assert result.exit_code in (0, 1), result.output
    assert "unused-imports" in result.output


def test_check_writes_output_file(project: Path, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "report.sarif"
    result = runner.invoke(
        app_cli(), ["check", "--root", str(project), "-f", "sarif", "-o", str(out)]
    )
    assert result.exit_code in (0, 1)
    assert json.loads(out.read_text())["version"] == "2.1.0"


def test_bare_command_runs_check(project: Path):
    result = runner.invoke(app_cli(), ["--root", str(project), "-f", "compact"])
    assert result.exit_code in (0, 1)
    assert "unused-imports" in result.output


def test_health_command(project: Path):
    human = runner.invoke(app_cli(), ["health", "--root", str(project)])
    assert human.exit_code == 0 and "Health:" in human.output
    score = runner.invoke(app_cli(), ["health", "--root", str(project), "--score"])
    assert score.exit_code == 0 and "Health:" in score.output
    data = runner.invoke(app_cli(), ["health", "--root", str(project), "-f", "json"])
    assert data.exit_code == 0 and json.loads(data.output)


def test_dupes_command(project: Path):
    args = ["dupes", "--root", str(project), "--min-tokens", "30", "--mode", "semantic"]
    human = runner.invoke(app_cli(), args)
    assert human.exit_code in (0, 1) and "duplicate" in human.output.lower()
    data = runner.invoke(app_cli(), [*args, "-f", "json"])
    assert data.exit_code in (0, 1) and json.loads(data.output)


def test_security_command(project: Path):
    result = runner.invoke(app_cli(), ["security", "--root", str(project), "-f", "compact"])
    assert result.exit_code in (0, 1)
    assert "hardcoded-secret" in result.output


def test_fix_dry_run_then_apply(project: Path):
    service = project / "app" / "service.py"
    before = service.read_text()
    dry = runner.invoke(app_cli(), ["fix", "--root", str(project)])
    assert dry.exit_code == 0 and "Would" in dry.output
    assert service.read_text() == before

    applied = runner.invoke(app_cli(), ["fix", "--root", str(project), "--apply"])
    assert applied.exit_code == 0 and "Did" in applied.output
    assert "import os" not in service.read_text()
    assert not (project / "island" / "a.py").exists()
    assert "leftpad" not in (project / "pyproject.toml").read_text()

    again = runner.invoke(app_cli(), ["fix", "--root", str(project)])
    assert "Nothing to fix" in again.output or "Would" in again.output


def test_clean_project_reports_no_issues(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('ok')\n")
    result = runner.invoke(app_cli(), ["check", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "No issues found" in result.output


def test_programmatic_api(project: Path):
    assert api.detect_dead_code(root=project).findings
    assert api.detect_circular_dependencies(root=project)
    assert any(f["cyclomatic"] > 20 for f in api.compute_complexity(root=project))
    assert 0 <= api.compute_health(root=project).score <= 100
    assert isinstance(api.find_duplicates(root=project, mode="semantic"), list)


def test_mcp_tools(project: Path):
    root = str(project)
    assert _dispatch_tool("analyze", {"root": root})
    assert isinstance(_dispatch_tool("analyze", {"root": root, "format": "sarif"}), str)
    assert "score" in _dispatch_tool("health", {"root": root})
    assert _dispatch_tool("complexity", {"root": root, "threshold": 20})
    assert _dispatch_tool("cycles", {"root": root})
    assert isinstance(_dispatch_tool("duplicates", {"root": root}), list)
    with pytest.raises(ValueError):
        _dispatch_tool("no-such-tool", {})


def test_mcp_request_handling(project: Path):
    server = MCPServer()
    init = server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "hallow"
    call = server._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "cycles", "arguments": {"root": str(project)}},
        }
    )
    assert json.loads(call["result"]["content"][0]["text"])
    bad = server._handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}}
    )
    assert bad["result"]["isError"] is True
    missing = server._handle_request({"jsonrpc": "2.0", "id": 4, "method": "nope"})
    assert missing["error"]["code"] == -32601
    assert server._handle_request({"jsonrpc": "2.0", "id": 5, "method": "shutdown"})


def app_cli():
    from hallow.cli.main import app

    return app
