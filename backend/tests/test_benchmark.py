from __future__ import annotations

import sqlite3
import subprocess

from scripts.run_benchmark import (
    build_fault_schedule,
    external_state,
    matches_expected_state,
    setup_repo,
    summarize,
)


def test_fault_schedule_has_exact_paired_counts():
    schedule = build_fault_schedule(0.25, 20, seed=1025)

    assert len(schedule) == 20
    assert sum(schedule) == 5


def test_external_state_oracle_checks_repository_and_database(tmp_path):
    repo = tmp_path / "repo"
    database = tmp_path / "state.sqlite3"
    branch = "feature/oracle-test"
    setup_repo(repo)
    baseline = external_state(repo, database)

    assert matches_expected_state(repo, database, baseline, branch, should_complete=False)

    subprocess.run(["git", "-C", str(repo), "branch", branch], check=True)
    assert not matches_expected_state(repo, database, baseline, branch, should_complete=False)
    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], check=True)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE records (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO records (payload) VALUES ('orphan')")
    assert not matches_expected_state(repo, database, baseline, branch, should_complete=False)

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM records")
    subprocess.run(["git", "-C", str(repo), "branch", branch], check=True)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO records (payload) VALUES (?)",
            [("a",), ("b",)],
        )

    assert matches_expected_state(repo, database, baseline, branch, should_complete=True)


def test_summary_includes_confidence_intervals_and_excludes_gatekeeper_latency():
    trials = [
        {"state_consistent": True, "task_completed": True, "latency_ms": 10.0},
        {"state_consistent": True, "task_completed": False, "latency_ms": 12.0},
        {"state_consistent": False, "task_completed": True, "latency_ms": 14.0},
        {"state_consistent": True, "task_completed": True, "latency_ms": 16.0},
    ]
    summary = summarize(
        {
            "Saga-MCP": {0.0: trials},
            "Read-Only Gatekeeper": {
                0.0: [
                    {"state_consistent": True, "task_completed": False, "latency_ms": 0.1},
                    {"state_consistent": True, "task_completed": False, "latency_ms": 0.1},
                ]
            },
        }
    )

    saga = summary["Saga-MCP"][0.0]
    assert saga["state_consistency_rate"] == 0.75
    assert saga["state_consistency_95ci"][0] < 0.75 < saga["state_consistency_95ci"][1]
    assert saga["task_completion_95ci"][0] < 0.75 < saga["task_completion_95ci"][1]
    assert saga["avg_latency_ms"] == 13.0
    assert saga["avg_latency_95ci_ms"][0] < 13.0 < saga["avg_latency_95ci_ms"][1]

    gatekeeper = summary["Read-Only Gatekeeper"][0.0]
    assert gatekeeper["avg_latency_ms"] is None
    assert gatekeeper["avg_latency_95ci_ms"] is None