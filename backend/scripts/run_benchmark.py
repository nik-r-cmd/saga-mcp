"""
Section VI evaluation harness: sweeps fault injection rates (0%, 25%,
50%) across three REAL systems, all running the same real 2-step task
against real git + SQLite:

  1. UNPROTECTED  - raw MCP tool calls, no safety layer at all. If a
     step fails, whatever already ran stays as-is (orphaned state).
  2. READ-ONLY GATEKEEPER - mirrors MCP-Secure's L2 enforcement: blocks
     every mutating tool call outright. Nothing ever runs, so state is
     always consistent, at the cost of zero task completion.
  3. SAGA-MCP (this project) - runs the real SagaExecutor. On failure,
     compensating actions roll back whatever already succeeded.

Every trial is a REAL execution against a REAL throwaway git repo and
REAL SQLite DB - failure is genuinely induced (empty rows passed to
seed_database with probability = the sweep's failure rate), not staged
after the fact.

Run with:
    python -m scripts.run_benchmark

Outputs:
    - printed summary table
    - benchmark_results.json (raw data)
    - state_recovery_rate.png, latency_overhead.png, task_completion.png
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.orchestration.mcp_client import MultiServerToolInvoker, ServerConfig
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec, SagaStatus
from src.saga.registry import CompensationRegistry

FAILURE_RATES = [0.0, 0.25, 0.5]
TRIALS_PER_CONDITION = int(os.environ.get("BENCH_TRIALS", "20"))
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "benchmark_output" / "paper_final"
OUTPUT_DIR = Path(os.environ.get("BENCH_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
READ_ONLY_MUTATIONS = {"create_branch", "seed_database"}


def build_fault_schedule(rate, n, seed):
    k = round(rate * n)
    flags = [True] * k + [False] * (n - k)
    random.Random(seed).shuffle(flags)
    return flags


def _force_remove_readonly(func, path, exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def setup_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("bench\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)


def branch_exists(repo_dir: Path, branch_name: str) -> bool:
    result = subprocess.run(
        ["git", "--no-pager", "branch", "--list", branch_name],
        cwd=repo_dir, capture_output=True, text=True, timeout=15,
    )
    return branch_name in result.stdout


def external_state(repo_dir: Path, db_path: Path) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
    branches = subprocess.run(
        ["git", "--no-pager", "branch", "--format=%(refname:short)"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout
    if not db_path.exists():
        rows: tuple[str, ...] | None = ()
    else:
        try:
            with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
                rows = tuple(row[0] for row in connection.execute("SELECT payload FROM records ORDER BY id"))
        except sqlite3.Error:
            rows = None
    return tuple(sorted(branches.splitlines())), rows


def matches_expected_state(
    repo_dir: Path,
    db_path: Path,
    baseline: tuple[tuple[str, ...], tuple[str, ...] | None],
    branch_name: str,
    should_complete: bool,
) -> bool:
    branches, rows = external_state(repo_dir, db_path)
    if not should_complete:
        return (branches, rows) == baseline
    expected_branches = tuple(sorted((*baseline[0], branch_name)))
    return branches == expected_branches and rows == ("a", "b")


def build_invoker(repo_dir: Path, db_path: Path) -> MultiServerToolInvoker:
    invoker = MultiServerToolInvoker()
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.git_server", str(repo_dir)]),
        tool_names=["create_branch", "delete_branch"],
    )
    invoker.register_tools(
        ServerConfig(command=sys.executable, args=["-m", "src.mcp_servers.db_server", str(db_path)]),
        tool_names=["seed_database", "wipe_database"],
    )
    return invoker


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


def run_unprotected_trial(induce_failure: bool) -> dict:
    work_dir = Path(tempfile.mkdtemp(prefix="bench_unprotected_"))
    repo_dir, db_path = work_dir / "repo", work_dir / "bench.sqlite3"
    setup_repo(repo_dir)
    baseline = external_state(repo_dir, db_path)
    invoker = build_invoker(repo_dir, db_path)
    branch_name = f"feature/bench-{int(time.time() * 1e6)}"

    start = time.perf_counter()
    invoker("create_branch", {"branch_name": branch_name})
    rows = [] if induce_failure else ["a", "b"]
    result = invoker("seed_database", {"rows": rows})
    elapsed_ms = (time.perf_counter() - start) * 1000

    failed = result.get("status") == "error"
    state_consistent = matches_expected_state(
        repo_dir, db_path, baseline, branch_name, should_complete=not induce_failure
    )
    task_completed = not failed

    shutil.rmtree(work_dir, onerror=_force_remove_readonly)
    return {
        "state_consistent": state_consistent,
        "task_completed": task_completed,
        "latency_ms": elapsed_ms,
        "fault_observed": failed,
    }


def run_gatekeeper_trial(_induce_failure: bool) -> dict:
    work_dir = Path(tempfile.mkdtemp(prefix="bench_read_only_"))
    repo_dir, db_path = work_dir / "repo", work_dir / "bench.sqlite3"
    setup_repo(repo_dir)
    baseline = external_state(repo_dir, db_path)

    start = time.perf_counter()
    planned_steps = ("create_branch", "seed_database")
    blocked = any(tool_name in READ_ONLY_MUTATIONS for tool_name in planned_steps)
    elapsed_ms = (time.perf_counter() - start) * 1000
    state_consistent = external_state(repo_dir, db_path) == baseline

    shutil.rmtree(work_dir, onerror=_force_remove_readonly)
    return {
        "state_consistent": state_consistent,
        "task_completed": not blocked,
        "latency_ms": elapsed_ms,
        "fault_observed": False,
        "policy_rejected": blocked,
    }


def run_saga_mcp_trial(induce_failure: bool) -> dict:
    work_dir = Path(tempfile.mkdtemp(prefix="bench_saga_"))
    repo_dir, db_path = work_dir / "repo", work_dir / "bench.sqlite3"
    setup_repo(repo_dir)
    baseline = external_state(repo_dir, db_path)
    invoker = build_invoker(repo_dir, db_path)
    registry = build_registry()
    executor = SagaExecutor(registry, invoker)
    branch_name = f"feature/bench-{int(time.time() * 1e6)}"

    rows = [] if induce_failure else ["a", "b"]
    start = time.perf_counter()
    tracker = executor.run(
        [("create_branch", {"branch_name": branch_name}), ("seed_database", {"rows": rows})]
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    status = tracker.record.status
    state_consistent = status in (SagaStatus.COMPLETED, SagaStatus.ROLLED_BACK) and matches_expected_state(
        repo_dir, db_path, baseline, branch_name, should_complete=not induce_failure
    )
    task_completed = status == SagaStatus.COMPLETED

    shutil.rmtree(work_dir, onerror=_force_remove_readonly)
    return {
        "state_consistent": state_consistent,
        "task_completed": task_completed,
        "latency_ms": elapsed_ms,
        "fault_observed": induce_failure,
        "saga_status": status.value,
    }


SYSTEMS = {
    "Unprotected": run_unprotected_trial,
    "Read-Only Gatekeeper": run_gatekeeper_trial,
    "Saga-MCP": run_saga_mcp_trial,
}


def run_sweep() -> tuple[dict, dict]:
    results: dict[str, dict[float, list[dict]]] = {name: {} for name in SYSTEMS}
    schedules = {r: build_fault_schedule(r, TRIALS_PER_CONDITION, 1000 + int(r * 100)) for r in FAILURE_RATES}

    for system_name, trial_fn in SYSTEMS.items():
        for rate in FAILURE_RATES:
            trials = []
            for i in range(TRIALS_PER_CONDITION):
                induce_failure = schedules[rate][i]
                trial_result = trial_fn(induce_failure)
                trial_result["trial_index"] = i + 1
                trial_result["fault_assigned"] = induce_failure
                trials.append(trial_result)
                print(
                    f"[{system_name}] rate={rate:.0%} trial={i + 1}/{TRIALS_PER_CONDITION} "
                    f"failure_induced={induce_failure} -> "
                    f"consistent={trial_result['state_consistent']} "
                    f"completed={trial_result['task_completed']}"
                )
            results[system_name][rate] = trials

    return results, schedules


def summarize(results: dict) -> dict:
    summary = {}
    for system_name, by_rate in results.items():
        summary[system_name] = {}
        for rate, trials in by_rate.items():
            n = len(trials)
            consistency_rate = sum(t["state_consistent"] for t in trials) / n
            completion_rate = sum(t["task_completed"] for t in trials) / n
            latency_comparable = system_name != "Read-Only Gatekeeper"
            avg_latency = sum(t["latency_ms"] for t in trials) / n if latency_comparable else None
            summary[system_name][rate] = {
                "state_consistency_rate": consistency_rate,
                "task_completion_rate": completion_rate,
                "avg_latency_ms": avg_latency,
                "latency_comparable": latency_comparable,
            }
    return summary


def print_table(summary: dict) -> None:
    print("\n" + "=" * 78)
    print(f"{'System':<24}{'Fail Rate':>12}{'Consistency':>14}{'Completion':>14}{'Latency(ms)':>14}")
    print("-" * 78)
    for system_name, by_rate in summary.items():
        for rate, metrics in by_rate.items():
            latency = "n/a" if metrics["avg_latency_ms"] is None else f"{metrics['avg_latency_ms']:.2f}"
            print(
                f"{system_name:<24}{rate:>11.0%} "
                f"{metrics['state_consistency_rate']:>13.1%} "
                f"{metrics['task_completion_rate']:>13.1%} "
                f"{latency:>13}"
            )
    print("=" * 78)


def plot_charts(summary: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rates_pct = [r * 100 for r in FAILURE_RATES]

    plt.figure(figsize=(7, 5))
    for system_name, by_rate in summary.items():
        values = [by_rate[r]["state_consistency_rate"] * 100 for r in FAILURE_RATES]
        plt.plot(rates_pct, values, marker="o", label=system_name)
    plt.xlabel("Fault Injection Rate (%)")
    plt.ylabel("State Consistency Rate (%)")
    plt.title("State Consistency vs Fault Injection Rate")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.ylim(-5, 105)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "state_recovery_rate.png", dpi=600)
    plt.close()

    plt.figure(figsize=(7, 5))
    system_names = [name for name in summary if name != "Read-Only Gatekeeper"]
    avg_latencies = [
        sum(summary[s][r]["avg_latency_ms"] for r in FAILURE_RATES) / len(FAILURE_RATES)
        for s in system_names
    ]
    plt.bar(system_names, avg_latencies, color="gray")
    plt.ylabel("Avg Latency (ms)")
    plt.title("Average Execution Latency by System")
    plt.xticks(rotation=15)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "latency_overhead.png", dpi=600)
    plt.close()

    plt.figure(figsize=(7, 5))
    width = 0.25
    x = range(len(FAILURE_RATES))
    completion_systems = list(summary.keys())
    for i, system_name in enumerate(completion_systems):
        values = [summary[system_name][r]["task_completion_rate"] * 100 for r in FAILURE_RATES]
        plt.bar([xi + i * width for xi in x], values, width=width, label=system_name)
    plt.xticks([xi + width for xi in x], [f"{r:.0%}" for r in FAILURE_RATES])
    plt.xlabel("Fault Injection Rate")
    plt.ylabel("Task Completion Rate (%)")
    plt.title("Task Completion vs Safety Trade-off")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "task_completion.png", dpi=600)
    plt.close()

    print(f"\n[charts] saved to {OUTPUT_DIR}/")


def main() -> None:
    random.seed(42)
    trial_conditions = len(SYSTEMS) * len(FAILURE_RATES) * TRIALS_PER_CONDITION
    real_workflows = 2 * len(FAILURE_RATES) * TRIALS_PER_CONDITION
    print(
        f"[benchmark] {trial_conditions} trial conditions: {real_workflows} real MCP workflows "
        f"plus {trial_conditions - real_workflows} read-only policy evaluations\n"
    )

    results, schedules = run_sweep()
    summary = summarize(results)
    print_table(summary)
    plot_charts(summary)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_results = {
        "configuration": {
            "trials_per_condition": TRIALS_PER_CONDITION,
            "failure_rates": FAILURE_RATES,
            "fault_schedules": {str(rate): schedule for rate, schedule in schedules.items()},
            "trial_conditions": len(SYSTEMS) * len(FAILURE_RATES) * TRIALS_PER_CONDITION,
            "mcp_workflow_executions": 2 * len(FAILURE_RATES) * TRIALS_PER_CONDITION,
            "read_only_policy_evaluations": len(FAILURE_RATES) * TRIALS_PER_CONDITION,
            "read_only_trials_are_policy_evaluations": True,
            "state_oracle": "Git branch set and SQLite records table",
            "latency_clock": "time.perf_counter",
            "latency_comparison": ["Unprotected", "Saga-MCP"],
            "llm_inference": False,
        },
        "summary": summary,
        "trials": results,
    }
    with open(OUTPUT_DIR / "benchmark_results.json", "w") as f:
        json.dump(raw_results, f, indent=2)
    print(f"[data] raw summary saved to {OUTPUT_DIR / 'benchmark_results.json'}")


if __name__ == "__main__":
    main()