"""
Pure, testable logic used by the local agent (saga_agent.py). Separated
from the WebSocket/networking loop so it can be tested directly without
spinning up a live connection - the networking layer just calls these
functions.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.discovery.auto_pair import AutoPairSuggester
from src.discovery.tool_discovery import DiscoveredTool, discover_tools
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry


def build_registry_from_api_response(approved: list[dict]) -> CompensationRegistry:
    """Turns the raw JSON from GET /registry into a real, usable
    CompensationRegistry - the same class SagaExecutor requires."""
    registry = CompensationRegistry()
    for pairing in approved:
        category = (
            ActionCategory.COMPENSABLE if pairing["category"] == "compensable" else ActionCategory.PIVOT
        )
        registry.register(
            ActionSpec(
                tool_name=pairing["tool_name"],
                category=category,
                compensating_tool=pairing.get("compensating_tool"),
            )
        )
    return registry


def fetch_registry(backend_http_url: str, token: str) -> CompensationRegistry:
    response = httpx.get(
        f"{backend_http_url}/registry",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    response.raise_for_status()
    return build_registry_from_api_response(response.json()["approved"])


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
