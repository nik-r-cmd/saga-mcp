"""
Tests for Stage 6a (discovery + auto-pairing). The integration test
proves discovery works against a REAL MCP server (our own git_server),
not a fake tool list - the exact same code path that will run against
someone else's real MCP server in production.
"""

from __future__ import annotations

import sys

import pytest

from src.discovery.auto_pair import AutoPairSuggester
from src.discovery.tool_discovery import DiscoveredTool, discover_tools


# ---------------------------------------------------------------------
# Auto-pairing unit tests (fast, no subprocess)
# ---------------------------------------------------------------------

def test_create_delete_pair_suggested_with_high_confidence():
    tools = [
        DiscoveredTool(name="create_branch", description="", input_schema={}),
        DiscoveredTool(name="delete_branch", description="", input_schema={}),
    ]
    suggestions = AutoPairSuggester().suggest(tools)
    by_name = {s.tool_name: s for s in suggestions}

    assert by_name["create_branch"].suggested_category == "compensable"
    assert by_name["create_branch"].suggested_compensating_tool == "delete_branch"
    assert by_name["create_branch"].confidence == "high"


def test_irreversible_verb_flagged_as_pivot():
    tools = [DiscoveredTool(name="send_email", description="", input_schema={})]
    suggestions = AutoPairSuggester().suggest(tools)

    assert suggestions[0].suggested_category == "pivot"
    assert suggestions[0].suggested_compensating_tool is None
    assert suggestions[0].confidence == "high"


def test_unrecognized_tool_defaults_to_pivot_low_confidence():
    """Safety property: an unknown tool defaults to PIVOT (safe), never
    to compensable-by-guess (unsafe)."""
    tools = [DiscoveredTool(name="frobnicate_widget", description="", input_schema={})]
    suggestions = AutoPairSuggester().suggest(tools)

    assert suggestions[0].suggested_category == "pivot"
    assert suggestions[0].confidence == "low"


def test_seed_wipe_pair_suggested():
    tools = [
        DiscoveredTool(name="seed_database", description="", input_schema={}),
        DiscoveredTool(name="wipe_database", description="", input_schema={}),
    ]
    suggestions = AutoPairSuggester().suggest(tools)
    by_name = {s.tool_name: s for s in suggestions}

    assert by_name["seed_database"].suggested_compensating_tool == "wipe_database"
    assert by_name["wipe_database"].suggested_compensating_tool == "seed_database"


# ---------------------------------------------------------------------
# Real, live discovery against our own actual MCP server (integration)
# ---------------------------------------------------------------------

@pytest.mark.integration
def test_real_discovery_against_our_own_git_server(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    tools = discover_tools(
        command=sys.executable,
        args=["-m", "src.mcp_servers.git_server", str(repo_dir)],
    )
    names = {t.name for t in tools}

    assert "create_branch" in names
    assert "delete_branch" in names
    assert "commit_file" in names
    assert "revert_last_commit" in names

    # Every discovered tool should carry a non-empty description, since
    # our servers declare docstrings - this catches a broken/empty
    # schema before it ever reaches the UI.
    for tool in tools:
        assert tool.description != ""


@pytest.mark.integration
def test_real_discovery_output_feeds_correctly_into_auto_pair(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    tools = discover_tools(
        command=sys.executable,
        args=["-m", "src.mcp_servers.git_server", str(repo_dir)],
    )
    suggestions = AutoPairSuggester().suggest(tools)
    by_name = {s.tool_name: s for s in suggestions}

    assert by_name["create_branch"].suggested_compensating_tool == "delete_branch"
    assert by_name["commit_file"].suggested_compensating_tool == "revert_last_commit"
