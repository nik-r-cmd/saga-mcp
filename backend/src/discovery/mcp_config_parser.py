"""
Shared parsing logic for the "mcpServers" JSON shape used by Claude
Desktop, Cursor, and other MCP clients. Both the local filesystem
scanner (repo_scanner.py) and the GitHub API scanner (github_scanner.py)
call this - one parser, two ways of getting file content into it, so
there's no risk of the two scanners silently drifting apart in what
they accept as valid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Known MCP config filenames/locations, checked relative to a repo root,
# regardless of whether the repo is on local disk or fetched via the
# GitHub API.
KNOWN_CONFIG_PATHS = [
    "mcp.json",
    ".mcp/config.json",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
]


@dataclass
class DiscoveredServerConfig:
    server_name: str
    command: str
    args: list[str]
    source_file: str  # relative path, for showing the user where this came from


def parse_mcp_config(content: str, source_file: str) -> list[DiscoveredServerConfig]:
    """Parses the raw text content of a candidate MCP config file.
    Returns an empty list (never raises) on malformed/unexpected content
    - a single bad config file must not abort an entire scan."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return []

    results = []
    for name, definition in servers.items():
        if not isinstance(definition, dict):
            continue
        command = definition.get("command")
        args = definition.get("args", [])
        if not command or not isinstance(args, list):
            continue
        results.append(
            DiscoveredServerConfig(
                server_name=name,
                command=command,
                args=[str(a) for a in args],
                source_file=source_file,
            )
        )
    return results
