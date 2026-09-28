from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from agent.agent_logic import build_registry_from_api_response, run_discovery_for_servers
from src.saga.models import ActionCategory


def test_build_registry_from_api_response():
    approved = [
        {"tool_name": "create_branch", "category": "compensable", "compensating_tool": "delete_branch"},
        {"tool_name": "send_email", "category": "pivot", "compensating_tool": None},
    ]
    registry = build_registry_from_api_response(approved)

    assert registry.get("create_branch").category == ActionCategory.COMPENSABLE
    assert registry.get("create_branch").compensating_tool == "delete_branch"
    assert registry.get("send_email").category == ActionCategory.PIVOT


def test_run_discovery_for_servers_against_real_git_server(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    servers = [
        {
            "server_name": "git",
            "command": sys.executable,
            "args": ["-m", "src.mcp_servers.git_server", str(repo_dir)],
        }
    ]
    results = run_discovery_for_servers(servers)

    assert "git" in results
    tool_names = {t["name"] for t in results["git"]}
    assert "create_branch" in tool_names
    assert "delete_branch" in tool_names
    create_branch = next(t for t in results["git"] if t["name"] == "create_branch")
    assert create_branch["suggested_compensating_tool"] == "delete_branch"


def test_run_discovery_handles_broken_server_gracefully():
    servers = [{"server_name": "broken", "command": "this-command-does-not-exist-anywhere", "args": []}]
    results = run_discovery_for_servers(servers)
    assert "error" in results["broken"]


def test_run_discovery_continues_after_one_server_fails(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    servers = [
        {"server_name": "broken", "command": "nonexistent-command-xyz", "args": []},
        {
            "server_name": "git",
            "command": sys.executable,
            "args": ["-m", "src.mcp_servers.git_server", str(repo_dir)],
        },
    ]
    results = run_discovery_for_servers(servers)

    assert "error" in results["broken"]
    assert "git" in results
    assert len(results["git"]) > 0  # the working server still returned real results
