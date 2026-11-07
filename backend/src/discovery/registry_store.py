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
    compensation_result_arg_mapping: dict[str, str] | None = None
    compensation_idempotent: bool = False
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
        try:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'approved_registry'"
            ).fetchone()
            if not table_exists:
                self._create_schema(conn)
                conn.commit()
                return

            info = conn.execute("PRAGMA table_info(approved_registry)").fetchall()
            columns = {row[1] for row in info}
            primary_key = [
                row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]
            ]
            if "user_id" in columns and primary_key == ["user_id", "tool_name"]:
                migrations = {
                    "server_command": "ALTER TABLE approved_registry ADD COLUMN server_command TEXT",
                    "server_args": "ALTER TABLE approved_registry ADD COLUMN server_args TEXT NOT NULL DEFAULT '[]'",
                    "compensation_arg_mapping": "ALTER TABLE approved_registry ADD COLUMN compensation_arg_mapping TEXT",
                    "compensation_result_arg_mapping": "ALTER TABLE approved_registry ADD COLUMN compensation_result_arg_mapping TEXT",
                    "compensation_idempotent": "ALTER TABLE approved_registry ADD COLUMN compensation_idempotent INTEGER NOT NULL DEFAULT 0",
                    "tool_description": "ALTER TABLE approved_registry ADD COLUMN tool_description TEXT NOT NULL DEFAULT ''",
                    "input_schema": "ALTER TABLE approved_registry ADD COLUMN input_schema TEXT NOT NULL DEFAULT '{}'",
                }
                for column, statement in migrations.items():
                    if column not in columns:
                        conn.execute(statement)
                conn.commit()
                return

            conn.execute("BEGIN IMMEDIATE")
            legacy_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(approved_registry)")
            }
            expressions = {
                "user_id": "COALESCE(user_id, '')" if "user_id" in legacy_columns else "''",
                "tool_name": "tool_name",
                "category": "category",
                "compensating_tool": "compensating_tool" if "compensating_tool" in legacy_columns else "NULL",
                "server_command": "server_command" if "server_command" in legacy_columns else "NULL",
                "server_args": "COALESCE(server_args, '[]')" if "server_args" in legacy_columns else "'[]'",
                "compensation_arg_mapping": "compensation_arg_mapping" if "compensation_arg_mapping" in legacy_columns else "NULL",
                "compensation_result_arg_mapping": "compensation_result_arg_mapping" if "compensation_result_arg_mapping" in legacy_columns else "NULL",
                "compensation_idempotent": "COALESCE(compensation_idempotent, 0)" if "compensation_idempotent" in legacy_columns else "0",
                "tool_description": "COALESCE(tool_description, '')" if "tool_description" in legacy_columns else "''",
                "input_schema": "COALESCE(input_schema, '{}')" if "input_schema" in legacy_columns else "'{}'",
            }
            legacy_rows = conn.execute(
                "SELECT " + ", ".join(expressions.values()) + " FROM approved_registry"
            ).fetchall()

            conn.execute("ALTER TABLE approved_registry RENAME TO approved_registry_legacy")
            self._create_schema(conn)
            conn.executemany(
                """
                INSERT INTO approved_registry (
                    user_id, tool_name, category, compensating_tool, server_command,
                    server_args, compensation_arg_mapping, compensation_result_arg_mapping,
                    compensation_idempotent, tool_description, input_schema
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                legacy_rows,
            )
            conn.execute("DROP TABLE approved_registry_legacy")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE approved_registry (
                user_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL,
                category TEXT NOT NULL,
                compensating_tool TEXT,
                server_command TEXT,
                server_args TEXT NOT NULL DEFAULT '[]',
                compensation_arg_mapping TEXT,
                compensation_result_arg_mapping TEXT,
                compensation_idempotent INTEGER NOT NULL DEFAULT 0,
                tool_description TEXT NOT NULL DEFAULT '',
                input_schema TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (user_id, tool_name)
            )
            """
        )

    def list_approvals(self, user_id: str = "") -> list[ApprovedPairing]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT tool_name, category, compensating_tool, server_command, server_args, "
            "compensation_arg_mapping, compensation_result_arg_mapping, tool_description, "
            "compensation_idempotent, input_schema FROM approved_registry "
            "WHERE user_id = ? ORDER BY tool_name",
            (user_id,),
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
                compensation_result_arg_mapping=json.loads(row[6]) if row[6] else None,
                tool_description=row[7] or "",
                compensation_idempotent=bool(row[8]),
                input_schema=json.loads(row[9] or "{}"),
            )
            for row in rows
        ]

    def approve(self, pairing: ApprovedPairing, user_id: str = "") -> None:
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
                user_id, tool_name, category, compensating_tool, server_command, server_args,
                compensation_arg_mapping, compensation_result_arg_mapping, tool_description,
                compensation_idempotent, input_schema
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, tool_name) DO UPDATE SET
                category = excluded.category,
                compensating_tool = excluded.compensating_tool,
                server_command = excluded.server_command,
                server_args = excluded.server_args,
                compensation_arg_mapping = excluded.compensation_arg_mapping,
                compensation_result_arg_mapping = excluded.compensation_result_arg_mapping,
                tool_description = excluded.tool_description,
                compensation_idempotent = excluded.compensation_idempotent,
                input_schema = excluded.input_schema
            """,
            (
                user_id,
                pairing.tool_name,
                pairing.category,
                pairing.compensating_tool,
                pairing.server_command,
                json.dumps(pairing.server_args),
                json.dumps(pairing.compensation_arg_mapping)
                if pairing.compensation_arg_mapping is not None
                else None,
                json.dumps(pairing.compensation_result_arg_mapping)
                if pairing.compensation_result_arg_mapping is not None
                else None,
                pairing.tool_description,
                int(pairing.compensation_idempotent),
                json.dumps(pairing.input_schema),
            ),
        )
        conn.commit()
        conn.close()

    def reject(self, tool_name: str, user_id: str = "") -> None:
        conn = self._connect()
        conn.execute(
            "DELETE FROM approved_registry WHERE tool_name = ? AND user_id = ?",
            (tool_name, user_id),
        )
        conn.commit()
        conn.close()

    def to_compensation_registry(self, user_id: str = "") -> CompensationRegistry:
        """Builds a live CompensationRegistry (the same class SagaExecutor
        uses) out of everything approved so far - the bridge between the
        discovery/approval UI and the actual saga engine."""
        registry = CompensationRegistry()
        for pairing in self.list_approvals(user_id):
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
                compensation_result_arg_mapping=pairing.compensation_result_arg_mapping,
                idempotent=pairing.compensation_idempotent,
            ))
        return registry
