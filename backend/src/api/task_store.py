from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class TaskResultStore:
    def __init__(self, path: Path | str = Path("logs") / "task_results.sqlite3") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_results (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS task_results_user_idx ON task_results(user_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def create(self, request_id: str, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_results (request_id, user_id, status, result, updated_at)
                VALUES (?, ?, 'running', NULL, ?)
                """,
                (request_id, user_id, time.time()),
            )

    def complete(self, request_id: str, user_id: str, result: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_results SET status = 'completed', result = ?, updated_at = ?
                WHERE request_id = ? AND user_id = ? AND status = 'running'
                """,
                (json.dumps(result), time.time(), request_id, user_id),
            )

    def get(self, request_id: str, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, result FROM task_results
                WHERE request_id = ? AND user_id = ?
                """,
                (request_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return {"status": row[0], "result": json.loads(row[1]) if row[1] else None}

    def fail_running(self, user_id: str | None = None) -> None:
        query = "UPDATE task_results SET status = 'failed', updated_at = ? WHERE status = 'running'"
        parameters: tuple[Any, ...] = (time.time(),)
        if user_id is not None:
            query += " AND user_id = ?"
            parameters += (user_id,)
        with self._connect() as connection:
            connection.execute(query, parameters)

    def prune_completed(self, retention_seconds: int = 7 * 24 * 60 * 60) -> None:
        cutoff = time.time() - retention_seconds
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM task_results WHERE status != 'running' AND updated_at < ?",
                (cutoff,),
            )
