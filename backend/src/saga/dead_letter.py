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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DLQ_PATH = Path("logs") / "dead_letter_queue.sqlite3"


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
        self._conn = sqlite3.connect(path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def add(self, entry: DeadLetterEntry) -> None:
        row = asdict(entry)
        row["arguments"] = json.dumps(row["arguments"])
        row["resolved"] = int(row["resolved"])
        self._conn.execute(
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
        self._conn.commit()

    def list_unresolved(self) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM dead_letter_queue WHERE resolved = 0 ORDER BY timestamp"
        )
        return cur.fetchall()

    def resolve(self, entry_id: int) -> None:
        self._conn.execute(
            "UPDATE dead_letter_queue SET resolved = 1 WHERE id = ?", (entry_id,)
        )
        self._conn.commit()

    def count_unresolved(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM dead_letter_queue WHERE resolved = 0"
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
