"""
Pure, testable logic used by the local agent (saga_agent.py). Separated
from the WebSocket/networking loop so it can be tested directly without
spinning up a live connection - the networking layer just calls these
functions.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from src.discovery.auto_pair import AutoPairSuggester
from src.discovery.tool_discovery import DiscoveredTool, discover_tools
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.logging_utils.audit_log import AuditLog
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry
from src.saga.dead_letter import DeadLetterQueue
from src.saga.executor import SagaExecutor


class SupabaseSessionError(RuntimeError):
    pass


def build_registry_from_api_response(approved: list[dict]) -> CompensationRegistry:
    """Turns the raw JSON from GET /registry into a real, usable
    CompensationRegistry - the same class SagaExecutor requires."""
    registry = CompensationRegistry()
    for pairing in approved:
        category = (
            ActionCategory.COMPENSABLE if pairing["category"] == "compensable" else ActionCategory.PIVOT
        )
        mapping = pairing.get("compensation_arg_mapping")
        compensation_arg_mapper = None
        if mapping is not None:
            compensation_arg_mapper = lambda arguments, mapping=mapping: {
                target: arguments[source] for target, source in mapping.items()
            }
        registry.register(ActionSpec(
            tool_name=pairing["tool_name"],
            category=category,
            compensating_tool=pairing.get("compensating_tool"),
            idempotent=bool(pairing.get("compensation_idempotent", False)),
            compensation_arg_mapper=compensation_arg_mapper,
            compensation_result_arg_mapping=pairing.get("compensation_result_arg_mapping"),
        ))
    return registry


def fetch_registry(backend_http_url: str, token: str) -> CompensationRegistry:
    return build_registry_from_api_response(fetch_registry_entries(backend_http_url, token))


def _authorized_request(
    method: str,
    token: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> httpx.Response:
    request_headers = dict(headers or {})
    current_token = os.environ.get("SAGA_SUPABASE_TOKEN", token)
    request_headers["Authorization"] = f"Bearer {current_token}"
    response = httpx.request(method, url, headers=request_headers, timeout=timeout)
    if response.status_code != 401:
        response.raise_for_status()
        return response

    refresh_token = os.environ.get("SAGA_SUPABASE_REFRESH_TOKEN")
    project_url = os.environ.get("SAGA_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    publishable_key = (
        os.environ.get("SAGA_SUPABASE_PUBLISHABLE_KEY")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    )
    missing = [
        name
        for name, value in (
            ("SAGA_SUPABASE_REFRESH_TOKEN", refresh_token),
            ("SAGA_SUPABASE_URL or SUPABASE_URL", project_url),
            (
                "SAGA_SUPABASE_PUBLISHABLE_KEY or SUPABASE_PUBLISHABLE_KEY",
                publishable_key,
            ),
        )
        if not value
    ]
    if missing:
        detail = response.text[:300]
        raise SupabaseSessionError(
            "The backend rejected the access token (401), and token refresh is not "
            f"configured. Set {', '.join(missing)} in this PowerShell window. "
            "Get the refresh token from the same signed-in Supabase session; "
            "do not paste it into chat. "
            f"Backend detail: {detail}"
        )

    refresh_response = httpx.post(
        f"{project_url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "refresh_token"},
        headers={"apikey": publishable_key},
        json={"refresh_token": refresh_token},
        timeout=timeout,
    )
    if refresh_response.is_error:
        raise SupabaseSessionError(
            "Supabase could not refresh this session. Sign in to the frontend again "
            "and copy the current session's access_token and refresh_token. "
            f"Supabase detail: {refresh_response.text[:300]}"
        )
    refreshed = refresh_response.json()
    access_token = refreshed.get("access_token")
    new_refresh_token = refreshed.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Supabase did not return a refreshed access token.")
    if not isinstance(new_refresh_token, str) or not new_refresh_token:
        raise ValueError("Supabase did not return a rotated refresh token.")

    os.environ["SAGA_SUPABASE_TOKEN"] = access_token
    os.environ["SAGA_SUPABASE_REFRESH_TOKEN"] = new_refresh_token
    request_headers["Authorization"] = f"Bearer {access_token}"
    response = httpx.request(method, url, headers=request_headers, timeout=timeout)
    if response.status_code == 401:
        raise SupabaseSessionError(
            "The refreshed Supabase access token was also rejected by the backend. "
            "Confirm both apps use the same Supabase project, sign in again, and "
            f"copy its current session tokens. Backend detail: {response.text[:300]}"
        )
    response.raise_for_status()
    return response


def fetch_registry_entries(backend_http_url: str, token: str) -> list[dict[str, Any]]:
    response = _authorized_request(
        "GET",
        token,
        f"{backend_http_url}/registry",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["approved"]


def fetch_websocket_ticket(backend_http_url: str, token: str) -> str:
    response = _authorized_request(
        "POST",
        token,
        f"{backend_http_url.rstrip('/')}/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    ticket = response.json().get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise ValueError("The backend did not return a WebSocket ticket.")
    return ticket


def build_tool_descriptions(approved: list[dict[str, Any]], allowed_tool_names: set[str]) -> dict[str, str]:
    descriptions = {}
    for pairing in approved:
        name = pairing["tool_name"]
        if name not in allowed_tool_names:
            continue
        description = pairing.get("tool_description") or name
        schema = pairing.get("input_schema") or {}
        if schema:
            description += f" Input schema: {json.dumps(schema, sort_keys=True)}"
        descriptions[name] = description
    return descriptions


def build_tool_schemas(approved: list[dict[str, Any]], allowed_tool_names: set[str]) -> dict[str, dict[str, Any]]:
    return {
        pairing["tool_name"]: pairing.get("input_schema") or {}
        for pairing in approved
        if pairing["tool_name"] in allowed_tool_names
    }


def format_discovered_tools(tools: list[DiscoveredTool]) -> list[dict[str, Any]]:
    """Same shape the backend's own discovery endpoints return, so the
    frontend renders agent-sourced results identically to backend-sourced
    ones."""
    suggestions = AutoPairSuggester().suggest(tools)
    suggestion_by_name = {s.tool_name: s for s in suggestions}
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "suggested_category": suggestion_by_name[t.name].suggested_category,
            "suggested_compensating_tool": suggestion_by_name[t.name].suggested_compensating_tool,
            "confidence": suggestion_by_name[t.name].confidence,
        }
        for t in tools
    ]


def run_discovery_for_servers(servers: list[dict]) -> dict[str, Any]:
    """servers: list of {"server_name", "command", "args", ...} as sent
    by the backend after a GitHub scan. Runs REAL discovery (spawns each
    server, calls tools/list) locally on the agent's machine - this is
    the actual code-execution step, happening in a trusted environment
    the user controls, not on the hosted backend."""
    results: dict[str, Any] = {}
    for server in servers:
        try:
            tools = discover_tools(server["command"], server["args"])
            results[server["server_name"]] = format_discovered_tools(tools)
        except Exception as exc:  # noqa: BLE001 - one broken server must
            # not stop discovery of the others; report the error, move on
            results[server["server_name"]] = {"error": str(exc)}
    return results


async def run_discovery_for_servers_async(servers: list[dict]) -> dict[str, Any]:
    """Run blocking MCP discovery in a worker so async callers stay responsive."""
    return await asyncio.to_thread(run_discovery_for_servers, servers)


def build_invoker_from_servers(servers: list[dict[str, Any]]) -> MultiServerToolInvoker:
    invoker = MultiServerToolInvoker()
    for server in servers:
        command = server.get("command")
        args = server.get("args")
        tool_names = server.get("tool_names")
        if not command or not isinstance(args, list) or not isinstance(tool_names, list):
            raise ValueError("Each MCP server registration needs command, args, and tool_names.")
        invoker.register_tools(ServerConfig(command=command, args=args), tool_names=tool_names)
    return invoker


def build_invoker_from_approvals(
    approved: list[dict[str, Any]],
) -> tuple[MultiServerToolInvoker, list[str]]:
    """Build execution routing only from server commands saved with approvals."""
    grouped: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    available_tools: list[str] = []
    for pairing in approved:
        command = pairing.get("server_command")
        args = pairing.get("server_args")
        if not isinstance(command, str) or not command or not isinstance(args, list):
            continue
        key = (command, tuple(args))
        server = grouped.setdefault(
            key, {"command": command, "args": args, "tool_names": []}
        )
        for tool_name in (pairing["tool_name"], pairing.get("compensating_tool")):
            if tool_name and tool_name not in server["tool_names"]:
                server["tool_names"].append(tool_name)
        if pairing["tool_name"] not in available_tools:
            available_tools.append(pairing["tool_name"])

    invoker = MultiServerToolInvoker()
    for server in grouped.values():
        invoker.register_tools(
            ServerConfig(command=server["command"], args=server["args"]),
            tool_names=server["tool_names"],
        )
    return invoker, available_tools


def run_rollback_demo(
    repo_path: str,
    database_path: str,
    registry: CompensationRegistry,
    event_emitter: Callable[[dict[str, Any]], None] | None = None,
):
    """Run a deterministic, real Git-success/SQLite-failure/ Git-rollback saga."""
    expected_compensations = {
        "create_branch": "delete_branch",
        "seed_database": "wipe_database",
    }
    for tool_name, compensating_tool in expected_compensations.items():
        spec = registry.get(tool_name)
        if spec.category != ActionCategory.COMPENSABLE or spec.compensating_tool != compensating_tool:
            raise ValueError(
                f"Rollback demo requires approved pairing {tool_name} -> {compensating_tool}."
            )

    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"Git repository directory does not exist: {repo}")
    git_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if git_check.returncode != 0:
        raise ValueError(f"Not a Git repository: {repo}")

    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(
            command=sys.executable,
            args=["-m", "src.mcp_servers.git_server", str(repo)],
        ),
        tool_names=["create_branch", "delete_branch"],
    )
    invoker.register_tools(
        ServerConfig(
            command=sys.executable,
            args=["-m", "src.mcp_servers.db_server", database_path],
        ),
        tool_names=["seed_database", "wipe_database"],
    )

    executor = SagaExecutor(
        registry,
        invoker,
        audit_log=AuditLog(),
        dead_letter_queue=DeadLetterQueue(),
        event_emitter=event_emitter,
    )
    return executor.run(
        [
            ("create_branch", {"branch_name": f"saga-demo/{uuid.uuid4().hex[:8]}"}),
            ("seed_database", {"rows": []}),
        ],
        agent_id="local-agent-demo",
    )
