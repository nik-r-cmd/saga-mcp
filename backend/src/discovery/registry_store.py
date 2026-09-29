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
import json
from dataclasses import dataclass, field
from pathlib import Path

from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry

DEFAULT_STORE_PATH = Path("logs") / "saga.db"


@dataclass
class ApprovedPairing:
    tool_name: str
    category: str  # "compensable" | "pivot"
    compensating_tool: str | None = None
    server_command: str | None = None
    server_args: list[str] = field(default_factory=list)
    compensation_arg_mapping: dict[str, str] | None = None
    tool_description: str = ""
    input_schema: dict = field(default_factory=dict)


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
                compensating_tool TEXT,
                server_command TEXT,
                server_args TEXT NOT NULL DEFAULT '[]',
                compensation_arg_mapping TEXT,
                tool_description TEXT NOT NULL DEFAULT '',
                input_schema TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(approved_registry)")}
        migrations = {
            "server_command": "ALTER TABLE approved_registry ADD COLUMN server_command TEXT",
            "server_args": "ALTER TABLE approved_registry ADD COLUMN server_args TEXT NOT NULL DEFAULT '[]'",
            "compensation_arg_mapping": "ALTER TABLE approved_registry ADD COLUMN compensation_arg_mapping TEXT",
            "tool_description": "ALTER TABLE approved_registry ADD COLUMN tool_description TEXT NOT NULL DEFAULT ''",
            "input_schema": "ALTER TABLE approved_registry ADD COLUMN input_schema TEXT NOT NULL DEFAULT '{}'",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)
        conn.commit()
        conn.close()

    def list_approvals(self) -> list[ApprovedPairing]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT tool_name, category, compensating_tool, server_command, server_args, "
            "compensation_arg_mapping, tool_description, input_schema FROM approved_registry"
        ).fetchall()
        conn.close()
        return [
            ApprovedPairing(
                tool_name=row[0],
                category=row[1],
                compensating_tool=row[2],
                server_command=row[3],
                server_args=json.loads(row[4] or "[]"),
                compensation_arg_mapping=json.loads(row[5]) if row[5] else None,
                tool_description=row[6] or "",
                input_schema=json.loads(row[7] or "{}"),
            )
            for row in rows
        ]

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
            INSERT INTO approved_registry (
                tool_name, category, compensating_tool, server_command, server_args,
                compensation_arg_mapping, tool_description, input_schema
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                category = excluded.category,
                compensating_tool = excluded.compensating_tool,
                server_command = excluded.server_command,
                server_args = excluded.server_args,
                compensation_arg_mapping = excluded.compensation_arg_mapping,
                tool_description = excluded.tool_description,
                input_schema = excluded.input_schema
            """,
            (
                pairing.tool_name,
                pairing.category,
                pairing.compensating_tool,
                pairing.server_command,
                json.dumps(pairing.server_args),
                json.dumps(pairing.compensation_arg_mapping)
                if pairing.compensation_arg_mapping is not None
                else None,
                pairing.tool_description,
                json.dumps(pairing.input_schema),
            ),
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
            mapper = None
            if pairing.compensation_arg_mapping is not None:
                mapping = pairing.compensation_arg_mapping

                def mapper(arguments: dict, mapping=mapping) -> dict:
                    return {target: arguments[source] for target, source in mapping.items()}

            registry.register(ActionSpec(
                tool_name=pairing.tool_name,
                category=category,
                compensating_tool=pairing.compensating_tool,
                compensation_arg_mapper=mapper,
            ))
        return registry
