from __future__ import annotations

import sqlite3
import subprocess

from scripts.run_benchmark import (
    build_fault_schedule,
    external_state,
    matches_expected_state,
    setup_repo,
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