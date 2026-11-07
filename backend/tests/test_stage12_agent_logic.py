from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

import agent.agent_logic as agent_logic
from agent.agent_logic import (
    SupabaseSessionError,
    build_invoker_from_approvals,
    build_registry_from_api_response,
    build_tool_descriptions,
    run_discovery_for_servers_async,
    run_discovery_for_servers,
)
from src.saga.models import ActionCategory


def test_registry_request_refreshes_expired_supabase_session(monkeypatch):
    monkeypatch.setenv("SAGA_SUPABASE_TOKEN", "expired-access-token")
    monkeypatch.setenv("SAGA_SUPABASE_REFRESH_TOKEN", "old-refresh-token")
    monkeypatch.setenv("SAGA_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SAGA_SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    requests = []

    def request(method, url, **kwargs):
        requests.append((method, url, {**kwargs, "headers": dict(kwargs["headers"])}))
        status = 401 if len(requests) == 1 else 200
        data = {} if status == 401 else {"approved": []}
        return httpx.Response(
            status,
            json=data,
            request=httpx.Request(method, url),
        )

    def refresh(url, **kwargs):
        assert url == "https://project.supabase.co/auth/v1/token"
        assert kwargs["params"] == {"grant_type": "refresh_token"}
        assert kwargs["json"] == {"refresh_token": "old-refresh-token"}
        assert kwargs["headers"] == {"apikey": "publishable-key"}
        return httpx.Response(
            200,
            json={"access_token": "new-access-token", "refresh_token": "new-refresh-token"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(agent_logic.httpx, "request", request)
    monkeypatch.setattr(agent_logic.httpx, "post", refresh)

    assert agent_logic.fetch_registry_entries("https://backend.example", "stale-argument") == []
    assert [call[2]["headers"]["Authorization"] for call in requests] == [
        "Bearer expired-access-token",
        "Bearer new-access-token",
    ]
    assert os.environ["SAGA_SUPABASE_TOKEN"] == "new-access-token"
    assert os.environ["SAGA_SUPABASE_REFRESH_TOKEN"] == "new-refresh-token"


def test_registry_request_reports_expired_token_without_refresh_configuration(monkeypatch):
    monkeypatch.delenv("SAGA_SUPABASE_TOKEN", raising=False)
    monkeypatch.delenv("SAGA_SUPABASE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("SAGA_SUPABASE_URL", raising=False)
    monkeypatch.delenv("SAGA_SUPABASE_PUBLISHABLE_KEY", raising=False)

    def request(method, url, **kwargs):
        return httpx.Response(
            401,
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(agent_logic.httpx, "request", request)

    with pytest.raises(SupabaseSessionError, match="SAGA_SUPABASE_REFRESH_TOKEN"):
        agent_logic.fetch_registry_entries("https://backend.example", "expired-token")


def test_build_registry_from_api_response():
    approved = [
        {"tool_name": "create_branch", "category": "compensable", "compensating_tool": "delete_branch"},
        {"tool_name": "send_email", "category": "pivot", "compensating_tool": None},
    ]
    registry = build_registry_from_api_response(approved)

    assert registry.get("create_branch").category == ActionCategory.COMPENSABLE
    assert registry.get("create_branch").compensating_tool == "delete_branch"
    assert registry.get("send_email").category == ActionCategory.PIVOT


def test_build_registry_restores_explicit_compensation_argument_mapping():
    registry = build_registry_from_api_response([
        {
            "tool_name": "commit_file",
            "category": "compensable",
            "compensating_tool": "revert_commit",
            "compensation_arg_mapping": {},
            "compensation_result_arg_mapping": {"commit_sha": "commit_sha"},
        }
    ])

    mapper = registry.get("commit_file").compensation_arg_mapper
    assert mapper is not None
    assert mapper({"path": "notes.txt", "content": "x", "message": "m"}) == {}
    assert registry.get("commit_file").compensation_result_arg_mapping == {
        "commit_sha": "commit_sha"
    }


def test_registry_restores_compensation_retry_approval():
    registry = build_registry_from_api_response(
        [
            {
                "tool_name": "seed_database",
                "category": "compensable",
                "compensating_tool": "wipe_database",
                "compensation_idempotent": True,
            }
        ]
    )

    assert registry.get("seed_database").idempotent is True


def test_task_invoker_uses_only_saved_approved_server_configurations():
    invoker, available_tools = build_invoker_from_approvals(
        [
            {
                "tool_name": "seed_database",
                "category": "compensable",
                "compensating_tool": "wipe_database",
                "server_command": sys.executable,
                "server_args": ["-m", "src.mcp_servers.db_server", "records.sqlite3"],
            },
            {
                "tool_name": "unconfigured",
                "category": "pivot",
                "server_command": None,
                "server_args": [],
            },
        ]
    )

    assert available_tools == ["seed_database"]
    assert set(invoker._tool_to_server) == {"seed_database", "wipe_database"}
    assert invoker._tool_to_server["wipe_database"].command == sys.executable


def test_tool_descriptions_include_discovered_schemas_for_allowed_tools():
    descriptions = build_tool_descriptions(
        [
            {
                "tool_name": "seed_database",
                "tool_description": "Insert records.",
                "input_schema": {"properties": {"rows": {"type": "array"}}},
            },
            {"tool_name": "orphaned", "tool_description": "No server config."},
        ],
        {"seed_database"},
    )

    assert "Insert records." in descriptions["seed_database"]
    assert '"rows"' in descriptions["seed_database"]
    assert "orphaned" not in descriptions


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


def test_discovering_sqlite_server_creates_database_automatically(tmp_path):
    database_path = tmp_path / "created" / "automatically" / "demo.sqlite3"
    servers = [
        {
            "server_name": "sqlite",
            "command": sys.executable,
            "args": ["-m", "src.mcp_servers.db_server", str(database_path)],
        }
    ]

    results = run_discovery_for_servers(servers)

    assert database_path.is_file()
    assert "sqlite" in results
    names = {tool["name"] for tool in results["sqlite"]}
    assert {"seed_database", "wipe_database", "count_rows"} <= names


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


def test_async_discovery_runs_mcp_client_outside_existing_event_loop(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    servers = [
        {
            "server_name": "git",
            "command": sys.executable,
            "args": ["-m", "src.mcp_servers.git_server", str(repo_dir)],
        }
    ]

    results = asyncio.run(run_discovery_for_servers_async(servers))

    assert "git" in results
    names = {tool["name"] for tool in results["git"]}
    assert {"create_branch", "delete_branch"} <= names
