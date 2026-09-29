"""
Integration test for the real MCP wiring - uses an actual git repo and
actual SQLite file, not mocks. This is slower than the unit tests
(spawns real subprocesses) but it's what actually proves the system
works end to end, which is the whole point of Stage 4b.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agent.agent_logic import build_invoker_from_servers, run_rollback_demo
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec, SagaStatus, StepStatus
from src.saga.registry import CompensationRegistry


@pytest.fixture()
def real_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_dir, check=True)
    return repo_dir


def _build_registry() -> CompensationRegistry:
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(
            tool_name="create_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="delete_branch",
            compensation_arg_mapper=lambda args: {"branch_name": args["branch_name"]},
        )
    )
    registry.register(
        ActionSpec(
            tool_name="delete_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="create_branch",
        )
    )
    registry.register(
        ActionSpec(
            tool_name="commit_file",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="revert_last_commit",
            compensation_arg_mapper=lambda args: {},
        )
    )
    registry.register(ActionSpec(tool_name="revert_last_commit", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    registry.register(
        ActionSpec(
            tool_name="seed_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="wipe_database",
            compensation_arg_mapper=lambda args: {},
        )
    )
    registry.register(
        ActionSpec(
            tool_name="wipe_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="seed_database",
        )
    )
    registry.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))
    return registry


def _build_reviewer_demo_registry() -> CompensationRegistry:
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(
            tool_name="create_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="delete_branch",
        )
    )
    registry.register(
        ActionSpec(
            tool_name="seed_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="wipe_database",
        )
    )
    return registry


def _build_invoker(repo_dir: Path, db_path: Path) -> MultiServerToolInvoker:
    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.git_server", str(repo_dir)]),
        tool_names=["create_branch", "delete_branch", "commit_file", "revert_last_commit"],
    )
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.db_server", str(db_path)]),
        tool_names=["seed_database", "wipe_database", "count_rows"],
    )
    return invoker


@pytest.mark.integration
def test_real_saga_success_leaves_branch_and_commit(real_repo, tmp_path):
    db_path = tmp_path / "test.sqlite3"
    executor = SagaExecutor(_build_registry(), _build_invoker(real_repo, db_path))

    tracker = executor.run(
        [
            ("create_branch", {"branch_name": "feature/ok"}),
            ("commit_file", {"path": "notes.txt", "content": "hi", "message": "add notes"}),
            ("seed_database", {"rows": ["a", "b"]}),
        ]
    )

    assert tracker.record.status == SagaStatus.COMPLETED
    branches = subprocess.run(["git", "branch"], cwd=real_repo, capture_output=True, text=True).stdout
    assert "feature/ok" in branches


@pytest.mark.integration
def test_real_saga_failure_genuinely_rolls_back_git_state(real_repo, tmp_path):
    db_path = tmp_path / "test.sqlite3"
    executor = SagaExecutor(_build_registry(), _build_invoker(real_repo, db_path))

    commits_before = subprocess.run(
        ["git", "log", "--oneline"], cwd=real_repo, capture_output=True, text=True
    ).stdout.strip().splitlines()

    tracker = executor.run(
        [
            ("create_branch", {"branch_name": "feature/fail"}),
            ("commit_file", {"path": "notes.txt", "content": "hi", "message": "add notes"}),
            ("seed_database", {"rows": []}),  # genuinely fails, empty rows
        ]
    )

    assert tracker.record.status == SagaStatus.ROLLED_BACK
    assert tracker.record.steps[0].status == StepStatus.COMPENSATED
    assert tracker.record.steps[1].status == StepStatus.COMPENSATED
    assert tracker.record.steps[2].status == StepStatus.FAILED

    # The real git state must be back to exactly what it was before.
    branches = subprocess.run(["git", "branch"], cwd=real_repo, capture_output=True, text=True).stdout
    assert "feature/fail" not in branches

    commits_after = subprocess.run(
        ["git", "log", "--oneline"], cwd=real_repo, capture_output=True, text=True
    ).stdout.strip().splitlines()
    assert commits_before == commits_after


@pytest.mark.integration
def test_reviewer_rollback_demo_runs_real_failure_and_inverse(real_repo, tmp_path):
    events = []
    tracker = run_rollback_demo(
        repo_path=str(real_repo),
        database_path=str(tmp_path / "reviewer-demo.sqlite3"),
        registry=_build_reviewer_demo_registry(),
        event_emitter=events.append,
    )

    assert tracker.record.status == SagaStatus.ROLLED_BACK
    assert [step.status for step in tracker.record.steps] == [StepStatus.COMPENSATED, StepStatus.FAILED]
    assert [event["event_type"] for event in events] == [
        "FORWARD_EXEC",
        "FAULT_DETECTED",
        "COMPENSATED",
    ]
    assert events[-1]["tool_name"] == "delete_branch"
    assert events[-1]["compensates"] == "create_branch"
    branches = subprocess.run(["git", "branch"], cwd=real_repo, capture_output=True, text=True).stdout
    assert "saga-demo/" not in branches


@pytest.mark.integration
def test_agent_invoker_routes_tools_to_independent_mcp_servers(real_repo, tmp_path):
    invoker = build_invoker_from_servers(
        [
            {
                "command": sys.executable,
                "args": ["-m", "src.mcp_servers.git_server", str(real_repo)],
                "tool_names": ["create_branch", "delete_branch"],
            },
            {
                "command": sys.executable,
                "args": ["-m", "src.mcp_servers.db_server", str(tmp_path / "multi.sqlite3")],
                "tool_names": ["seed_database", "wipe_database", "count_rows"],
            },
        ]
    )
    registry = _build_registry()

    tracker = SagaExecutor(registry, invoker).run(
        [
            ("create_branch", {"branch_name": "feature/multi-server"}),
            ("seed_database", {"rows": []}),
        ]
    )

    assert tracker.record.status == SagaStatus.ROLLED_BACK
    branches = subprocess.run(["git", "branch"], cwd=real_repo, capture_output=True, text=True).stdout
    assert "feature/multi-server" not in branches


@pytest.mark.integration
def test_invoker_resolves_python_to_current_environment(real_repo):
    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(command="python", args=["-m", "src.mcp_servers.git_server", str(real_repo)]),
        tool_names=["create_branch"],
    )

    result = invoker("create_branch", {"branch_name": "feature/current-python"})

    assert result["status"] == "ok"
