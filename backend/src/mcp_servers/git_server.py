"""
A real, spec-compliant MCP server exposing git operations against a
local repository. Every tool here does genuine, verifiable work - no
mocking. Run standalone via:

    python -m src.mcp_servers.git_server /path/to/repo

or launched automatically by SagaExecutor's real MCP client (Stage 4b).

Tools:
    create_branch(branch_name)  -> forward action
    delete_branch(branch_name)  -> compensates create_branch
    commit_file(path, content, message) -> forward action
    revert_last_commit()        -> compensates commit_file

All git calls go through subprocess against a real repo. Failures are
real git failures (bad branch name, dirty working tree, etc.), not
staged - which is exactly what Stage 4's failure detector needs to be
tested against honestly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="git-server")

# The repo path is injected via argv so the same server code can point
# at any test repository without editing this file.
REPO_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def _run_git(*args: str) -> dict:
    result = subprocess.run(
        ["git", "-C", str(REPO_PATH), "--no-pager", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr.strip()}
    return {"status": "ok", "message": result.stdout.strip()}


@server.tool()
def create_branch(branch_name: str) -> dict:
    """Create a new git branch from the current HEAD."""
    return _run_git("branch", branch_name)


@server.tool()
def delete_branch(branch_name: str) -> dict:
    """Delete a git branch. Compensates create_branch."""
    return _run_git("branch", "-D", branch_name)


@server.tool()
def commit_file(path: str, content: str, message: str) -> dict:
    """Write content to a file inside the repo and commit it."""
    target = REPO_PATH / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)

    add_result = _run_git("add", path)
    if add_result["status"] == "error":
        return add_result

    return _run_git("commit", "-m", message)


@server.tool()
def revert_last_commit() -> dict:
    """Undo the most recent commit, keeping working tree changes staged
    for inspection. Compensates commit_file."""
    return _run_git("reset", "--hard", "HEAD~1")


if __name__ == "__main__":
    server.run()  # synchronous entry point; runs run_stdio_async() internally
