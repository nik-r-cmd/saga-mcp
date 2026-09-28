"""
RegistryStore: persists ONLY human-approved tool pairings.

Migrated from a JSON file to SQLite (saga.db) for two real reasons, not
cosmetic ones:
  1. A JSON file rewritten by "read whole file, modify, write whole
     file" is not safe under concurrent requests - two simultaneous
     approvals from two browser tabs could race and one could silently
     overwrite the other's write. SQLite's file-level locking handles
     concurrent writes correctly.
  2. Matches what a "production-grade" persistence layer should look
     like for this project going forward.

Follows the same per-call-connection pattern as src/auth/store.py -
fixes the identical cross-thread SQLite issue FastAPI's threadpool
causes if a single connection were held open and reused.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry

DEFAULT_STORE_PATH = Path("logs") / "saga.db"


@dataclass
class ApprovedPairing:
    tool_name: str
    category: str  # "compensable" | "pivot"
    compensating_tool: str | None = None


class RegistryStore:
    def __init__(self, path: Path | str = DEFAULT_STORE_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approved_registry (
                tool_name TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                compensating_tool TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def list_approvals(self) -> list[ApprovedPairing]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT tool_name, category, compensating_tool FROM approved_registry"
        ).fetchall()
        conn.close()
        return [ApprovedPairing(tool_name=r[0], category=r[1], compensating_tool=r[2]) for r in rows]

    def approve(self, pairing: ApprovedPairing) -> None:
        if pairing.category == "compensable" and not pairing.compensating_tool:
            raise ValueError(
                f"Cannot approve '{pairing.tool_name}' as compensable without "
                "a compensating_tool. Mark it as 'pivot' instead if it truly "
                "has no undo action."
            )
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO approved_registry (tool_name, category, compensating_tool)
            VALUES (?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                category = excluded.category,
                compensating_tool = excluded.compensating_tool
            """,
            (pairing.tool_name, pairing.category, pairing.compensating_tool),
        )
        conn.commit()
        conn.close()

    def reject(self, tool_name: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM approved_registry WHERE tool_name = ?", (tool_name,))
        conn.commit()
        conn.close()

    def to_compensation_registry(self) -> CompensationRegistry:
        """Builds a live CompensationRegistry (the same class SagaExecutor
        uses) out of everything approved so far - the bridge between the
        discovery/approval UI and the actual saga engine."""
        registry = CompensationRegistry()
        for pairing in self.list_approvals():
            category = (
                ActionCategory.COMPENSABLE
                if pairing.category == "compensable"
                else ActionCategory.PIVOT
            )
            registry.register(
                ActionSpec(
                    tool_name=pairing.tool_name,
                    category=category,
                    compensating_tool=pairing.compensating_tool,
                )
            )
        return registry
