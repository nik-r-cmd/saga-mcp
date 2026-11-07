"""
Structured audit logging.

Every tool call, policy decision, and provenance check in this project
writes a row here. This is deliberately built early (Stage 0) because
every later stage's evaluation metrics (Stage 5) are computed by querying
this log, not by bolting on logging as an afterthought.

Each entry is a flat, queryable record - avoid nesting so this can later
be dumped straight into a pandas DataFrame or a dashboard table with no
transformation step.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from src.config import AUDIT_DB_PATH

Decision = Literal["ALLOWED", "BLOCKED", "NO_OP", "PENDING_APPROVAL"]


@dataclass
class AuditEntry:
    """One row in the audit trail. Keep this the single source of truth
    for what a 'logged event' looks like across the whole project."""

    timestamp: float
    agent_id: str
    tool_name: str
    action_type: str  # "read" | "write" | "sensitive"
    decision: Decision
    reason: str
    origin_agent_id: str | None = None  # set from Stage 4 onward
    latency_ms: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    """Thin wrapper around a local SQLite table. No ORM - this project is
    small enough that raw SQL is more transparent for evaluation queries."""

    def __init__(self, db_path=AUDIT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10)

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_trail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    agent_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    origin_agent_id TEXT,
                    latency_ms REAL,
                    extra TEXT
                )
                """
            )

    def record(self, entry: AuditEntry) -> None:
        row = asdict(entry)
        row["extra"] = json.dumps(row["extra"])
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_trail
                    (timestamp, agent_id, tool_name, action_type, decision,
                     reason, origin_agent_id, latency_ms, extra)
                VALUES (:timestamp, :agent_id, :tool_name, :action_type,
                        :decision, :reason, :origin_agent_id, :latency_ms, :extra)
                """,
                row,
            )

    def all_entries(self) -> list[sqlite3.Row]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM audit_trail ORDER BY id").fetchall()

    def close(self) -> None:
        return None


def now_ms() -> float:
    """Helper: current time in milliseconds, used for latency_ms fields."""
    return time.time() * 1000
