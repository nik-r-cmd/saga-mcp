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
import re
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
    repo = REPO_PATH.resolve()
    target = (repo / path).resolve()
    if not target.is_relative_to(repo) or target == repo:
        return {"status": "error", "message": "File path must stay inside the repository."}

    staged_changes = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
        capture_output=True,
        timeout=15,
    )
    if staged_changes.returncode != 0:
        return {"status": "error", "message": "Refusing to commit while unrelated changes are staged."}

    relative_path = target.relative_to(repo).as_posix()
    path_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", relative_path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if path_status.returncode != 0:
        return {"status": "error", "message": path_status.stderr.strip()}
    if path_status.stdout.strip():
        return {"status": "error", "message": "Refusing to overwrite a file with uncommitted changes."}

    tracked_status = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", relative_path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if tracked_status.returncode > 1:
        return {"status": "error", "message": tracked_status.stderr.strip()}
    if target.exists() and tracked_status.returncode != 0:
        return {"status": "error", "message": "Refusing to overwrite an existing untracked file."}

    existed = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    add_result = _run_git("add", "--", relative_path)
    if add_result["status"] == "error":
        cleanup_error = _restore_failed_commit(target, relative_path, existed)
        if cleanup_error:
            return {
                "status": "error",
                "message": f"{add_result['message']} Cleanup also failed: {cleanup_error}",
            }
        return add_result

    commit_result = _run_git("commit", "-m", message)
    if commit_result["status"] == "error":
        cleanup_error = _restore_failed_commit(target, relative_path, existed)
        if cleanup_error:
            return {
                "status": "error",
                "message": f"{commit_result['message']} Cleanup also failed: {cleanup_error}",
            }
        return commit_result

    commit_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    return {"status": "ok", "message": commit_result["message"], "commit_sha": commit_sha}


@server.tool()
def revert_commit(commit_sha: str) -> dict:
    """Create a new revert commit for the exact commit made by commit_file."""
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        return {"status": "error", "message": "A full Git commit hash is required."}
    repo = REPO_PATH.resolve()
    exists = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
        capture_output=True,
        timeout=15,
    )
    if exists.returncode != 0:
        return {"status": "error", "message": "The commit to revert does not exist in this repository."}
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit_sha, "HEAD"],
        capture_output=True,
        timeout=15,
    )
    if ancestor.returncode != 0:
        return {"status": "error", "message": "The commit to revert is not an ancestor of the current branch."}
    return _run_git("revert", "--no-edit", commit_sha)


def _restore_failed_commit(
    target: Path, relative_path: str, existed: bool
) -> str | None:
    errors = []
    if existed:
        result = _run_git(
            "restore", "--source=HEAD", "--staged", "--worktree", "--", relative_path
        )
        if result["status"] == "error":
            errors.append(result["message"])
    else:
        result = _run_git("rm", "--cached", "--ignore-unmatch", "--", relative_path)
        if result["status"] == "error":
            errors.append(result["message"])
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(str(exc))
    return "; ".join(errors) or None


if __name__ == "__main__":
    server.run()  # synchronous entry point; runs run_stdio_async() internally
