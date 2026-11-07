"""
DeadLetterQueue: persists steps whose compensation FAILED after all
retries were exhausted - the case where the saga engine could not undo
something it already did, and a human needs to intervene.

Separate from AuditLog on purpose: AuditLog is a complete history of
every decision (allowed/blocked/compensated/etc). The DLQ is
specifically the operator-facing worklist of "things that still need a
human," and entries here get explicitly resolved once handled - that
resolution workflow doesn't belong mixed into a general audit trail.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DLQ_PATH = Path(__file__).resolve().parents[2] / "logs" / "dead_letter_queue.sqlite3"


@dataclass
class DeadLetterEntry:
    saga_id: str
    step_id: str
    tool_name: str
    compensating_tool: str | None
    arguments: dict[str, Any]
    failure_detail: str
    timestamp: float
    compensation_attempts: int
    resolved: bool = False


class DeadLetterQueue:
    def __init__(self, path: Path | str = DEFAULT_DLQ_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

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
                CREATE TABLE IF NOT EXISTS dead_letter_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    saga_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    compensating_tool TEXT,
                    arguments TEXT NOT NULL,
                    failure_detail TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    compensation_attempts INTEGER NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def add(self, entry: DeadLetterEntry) -> None:
        row = asdict(entry)
        row["arguments"] = json.dumps(row["arguments"])
        row["resolved"] = int(row["resolved"])
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO dead_letter_queue
                    (saga_id, step_id, tool_name, compensating_tool, arguments,
                     failure_detail, timestamp, compensation_attempts, resolved)
                VALUES (:saga_id, :step_id, :tool_name, :compensating_tool,
                        :arguments, :failure_detail, :timestamp,
                        :compensation_attempts, :resolved)
                """,
                row,
            )

    def list_unresolved(self) -> list[sqlite3.Row]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM dead_letter_queue WHERE resolved = 0 ORDER BY timestamp"
            ).fetchall()

    def resolve(self, entry_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE dead_letter_queue SET resolved = 1 WHERE id = ?", (entry_id,)
            )

    def count_unresolved(self) -> int:
        with self._connection() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM dead_letter_queue WHERE resolved = 0"
            ).fetchone()[0]

    def close(self) -> None:
        return None
