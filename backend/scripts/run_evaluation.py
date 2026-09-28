"""
Evaluation harness - this is what produces the results table for your
report/paper. Runs a batch of REAL sagas against real git + SQLite:
some are legitimate tasks that should complete cleanly, some are
induced-failure tasks (empty rows) that must roll back cleanly.

Deliberately does NOT use the LLM planner here - evaluation needs
reproducible, deterministic step sequences so the numbers reflect the
saga engine's behavior, not model sampling variance. The LLM-driven path
is demonstrated separately in run_saga_demo_llm.py.

Run with:
    python -m scripts.run_evaluation

Increase RUNS_PER_CONDITION for a more statistically stable result (the
base paper you're extending used 15 repeats per configuration).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluation.metrics import RunResult, compute_report
from src.logging_utils.audit_log import AuditLog
from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry

RUNS_PER_CONDITION = 10  # bump to 15+ for your final report numbers


def _force_remove_readonly(func, path, exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def setup_test_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "eval@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("# eval repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo_dir, check=True)


def build_registry() -> CompensationRegistry:
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(
            tool_name="create_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="delete_branch",
            compensation_arg_mapper=lambda args: {"branch_name": args["branch_name"]},
        )
    )
    registry.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    registry.register(
        ActionSpec(
            tool_name="seed_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="wipe_database",
            compensation_arg_mapper=lambda args: {},
        )
    )
    registry.register(ActionSpec(tool_name="wipe_database", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    registry.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))
    return registry


def run_one(should_succeed: bool, audit_log: AuditLog) -> RunResult:
    work_dir = Path(tempfile.mkdtemp(prefix="saga_eval_"))
    repo_dir = work_dir / "repo"
    db_path = work_dir / "eval.sqlite3"
    setup_test_repo(repo_dir)

    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.git_server", str(repo_dir)]),
        tool_names=["create_branch", "delete_branch"],
    )
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.db_server", str(db_path)]),
        tool_names=["seed_database", "wipe_database"],
    )

    registry = build_registry()
    executor = SagaExecutor(registry, invoker, audit_log=audit_log)

    rows = ["a", "b"] if should_succeed else []  # empty rows = genuine,
    # real failure from the db_server tool itself, not staged

    start = time.time()
    tracker = executor.run(
        [
            ("create_branch", {"branch_name": f"feature/eval-{int(start * 1000)}"}),
            ("seed_database", {"rows": rows}),
        ],
        agent_id="eval_harness",
    )
    elapsed_ms = (time.time() - start) * 1000

    shutil.rmtree(work_dir, onerror=_force_remove_readonly)

    return RunResult(
        saga_status=tracker.record.status.value,
        should_succeed=should_succeed,
        latency_ms=elapsed_ms,
    )


def main() -> None:
    db_fd, db_file = tempfile.mkstemp(suffix=".sqlite3", prefix="eval_audit_")
    os.close(db_fd)
    audit_log = AuditLog(db_path=db_file)

    results: list[RunResult] = []

    print(f"[eval] running {RUNS_PER_CONDITION} legitimate runs (should complete)...")
    for i in range(RUNS_PER_CONDITION):
        results.append(run_one(should_succeed=True, audit_log=audit_log))
        print(f"  legitimate run {i + 1}/{RUNS_PER_CONDITION}: {results[-1].saga_status}")

    print(f"\n[eval] running {RUNS_PER_CONDITION} induced-failure runs (should roll back)...")
    for i in range(RUNS_PER_CONDITION):
        results.append(run_one(should_succeed=False, audit_log=audit_log))
        print(f"  induced-failure run {i + 1}/{RUNS_PER_CONDITION}: {results[-1].saga_status}")

    report = compute_report(results, audit_log)
    print("\n" + report.as_table())

    audit_log.close()
    os.remove(db_file)


if __name__ == "__main__":
    main()
