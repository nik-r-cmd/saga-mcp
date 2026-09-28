"""
LocalRepoScanner: walks a repo directory ON LOCAL DISK looking for known
MCP config files. This is the path used by the local agent (see
agent/saga_agent.py) which runs on the user's own machine and has
direct filesystem access - it does NOT require cloning anything, since
it's already running where the repo lives.

For scanning a GitHub repo the backend has never touched locally, see
github_scanner.py instead, which reads the same file shapes via the
GitHub API.
"""

from __future__ import annotations

from pathlib import Path

from src.discovery.mcp_config_parser import (
    KNOWN_CONFIG_PATHS,
    DiscoveredServerConfig,
    parse_mcp_config,
)

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}
_MAX_SCAN_DEPTH = 4


def scan_repo(repo_path: str | Path) -> list[DiscoveredServerConfig]:
    root = Path(repo_path)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"'{repo_path}' is not a directory that exists.")

    found: list[DiscoveredServerConfig] = []

    for rel_path in KNOWN_CONFIG_PATHS:
        candidate = root / rel_path
        if candidate.is_file():
            found.extend(_parse_local_file(candidate, root))

    already_found = {Path(f.source_file) for f in found}
    for path in _walk_bounded(root, depth=0):
        if path.name == "mcp.json" and path.relative_to(root) not in already_found:
            found.extend(_parse_local_file(path, root))

    return found


def _parse_local_file(path: Path, root: Path) -> list[DiscoveredServerConfig]:
    try:
        content = path.read_text()
    except (UnicodeDecodeError, PermissionError):
        return []
    return parse_mcp_config(content, source_file=str(path.relative_to(root)))


def _walk_bounded(directory: Path, depth: int):
    if depth > _MAX_SCAN_DEPTH:
        return
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            yield from _walk_bounded(entry, depth + 1)
        else:
            yield entry
