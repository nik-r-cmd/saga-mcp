"""
End-to-end Saga demo against REAL tools - a real git repository and a
real SQLite database. No mocking, no staged responses.

What this script does:
  1. Creates a throwaway git repo and SQLite DB under a temp directory
     (so you never need to hand-build a test repo yourself).
  2. Registers the real compensation pairs from Stage 4.
  3. Runs a saga: create_branch -> commit_file -> seed_database
  4. Deliberately seeds an EMPTY rows list, which the real db_server
     genuinely rejects (status=error) - not a scripted failure, an
     actual validation failure the tool itself produces.
  5. Watches the executor roll back commit_file and create_branch for
     real: revert_last_commit actually runs `git reset --hard`, and
     delete_branch actually runs `git branch -D`.
  6. Prints the before/after repo and DB state so you can see the
     rollback happened, not just read a log line claiming it did.

Run with:
    python -m scripts.run_saga_demo
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _force_remove_readonly(func, path, exc_info) -> None:
    """shutil.rmtree error handler for Windows.

    Git marks files inside .git/objects as read-only. On Linux/Mac this
    doesn't block deletion, but Windows' rmtree refuses to delete a
    read-only file unless we explicitly clear that attribute first. This
    handler does that and retries the operation once.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry


def setup_test_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Saga Demo"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("# saga demo repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo_dir, check=True)
    print(f"[setup] created test repo at {repo_dir}")


def print_repo_state(repo_dir: Path, label: str) -> None:
    branches = subprocess.run(
        ["git", "--no-pager", "branch"], cwd=repo_dir, capture_output=True, text=True, timeout=15
    ).stdout.strip()
    log = subprocess.run(
        ["git", "--no-pager", "log", "--oneline"], cwd=repo_dir, capture_output=True, text=True, timeout=15
    ).stdout.strip()
    print(f"\n--- repo state ({label}) ---")
    print("branches:\n" + branches)
    print("commits:\n" + log)


def main() -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="saga_demo_"))
    repo_dir = work_dir / "repo"
    db_path = work_dir / "saga_demo.sqlite3"

    setup_test_repo(repo_dir)
    print_repo_state(repo_dir, "before saga")

    # --- Wire real MCP servers ---
    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(
            command=sys.executable,
            args=["-m", "src.mcp_servers.git_server", str(repo_dir)],
        ),
        tool_names=["create_branch", "delete_branch", "commit_file", "revert_last_commit"],
    )
    invoker.register_tools(
        ServerConfig(
            command=sys.executable,
            args=["-m", "src.mcp_servers.db_server", str(db_path)],
        ),
        tool_names=["seed_database", "wipe_database", "count_rows"],
    )

    # --- Register real compensation pairs ---
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(
            tool_name="create_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="delete_branch",
            compensation_arg_mapper=lambda args: {"branch_name": args["branch_name"]},
        )
    )
    registry.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="noop_for_delete"))
    registry.register(
        ActionSpec(
            tool_name="commit_file",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="revert_last_commit",
            # revert_last_commit takes NO arguments - commit_file's
            # {path, content, message} must NOT be forwarded to it.
            compensation_arg_mapper=lambda args: {},
        )
    )
    registry.register(ActionSpec(tool_name="revert_last_commit", category=ActionCategory.COMPENSABLE, compensating_tool="noop_for_revert"))
    registry.register(
        ActionSpec(
            tool_name="seed_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="wipe_database",
            # wipe_database takes NO arguments - seed_database's {rows: [...]}
            # must NOT be forwarded to it.
            compensation_arg_mapper=lambda args: {},
        )
    )
    registry.register(ActionSpec(tool_name="wipe_database", category=ActionCategory.COMPENSABLE, compensating_tool="noop_for_wipe"))
    # noop_for_* are never actually invoked in this demo (nothing
    # compensates the compensators), but the registry requires every
    # COMPENSABLE tool to declare one - see registry.py docstring.
    for noop_name in ("noop_for_delete", "noop_for_revert", "noop_for_wipe"):
        registry.register(ActionSpec(tool_name=noop_name, category=ActionCategory.PIVOT))

    executor = SagaExecutor(registry, invoker)

    print("\n[saga] running: create_branch -> commit_file -> seed_database (rows=[])")
    print("[saga] seed_database is called with an EMPTY rows list on purpose -")
    print("[saga] the real db_server tool genuinely rejects this, no staging involved.\n")

    tracker = executor.run(
        [
            ("create_branch", {"branch_name": "feature/saga-demo"}),
            ("commit_file", {"path": "notes.txt", "content": "work in progress", "message": "add notes"}),
            ("seed_database", {"rows": []}),  # <- genuinely fails
        ]
    )

    print(f"[saga] final status: {tracker.record.status.value}")
    for step in tracker.record.steps:
        print(f"  - {step.tool_name}: {step.status.value} (reason={step.failure_reason.value})")

    print_repo_state(repo_dir, "after saga rollback")

    shutil.rmtree(work_dir, onerror=_force_remove_readonly)
    print(f"\n[cleanup] removed {work_dir}")


if __name__ == "__main__":
    main()
