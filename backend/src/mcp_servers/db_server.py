"""
A real MCP server exposing SQLite operations against a local database
file. Genuine reads/writes, genuine failure modes (e.g. seeding into a
table that doesn't exist, disk issues, constraint violations).

Tools:
    seed_database(rows)  -> forward action, inserts rows into a table
    wipe_database()      -> compensates seed_database, truncates the table
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="db-server")

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("saga_demo.sqlite3")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return conn


def initialize_database() -> None:
    with closing(_connect()) as conn:
        conn.commit()


@server.tool()
def seed_database(rows: list[str]) -> dict:
    """Insert a list of string payloads into the records table. Fails
    genuinely (returns status=error) if rows is empty or the DB is
    unreachable - used to demonstrate a real, unstaged saga failure."""
    if not rows:
        return {"status": "error", "message": "no rows provided to seed"}
    try:
        with closing(_connect()) as conn:
            record_ids = []
            for row in rows:
                cursor = conn.execute("INSERT INTO records (payload) VALUES (?)", (row,))
                record_ids.append(cursor.lastrowid)
            conn.commit()
            return {
                "status": "ok",
                "message": f"inserted {len(rows)} rows",
                "record_ids": record_ids,
            }
    except sqlite3.Error as exc:
        return {"status": "error", "message": str(exc)}


@server.tool()
def wipe_database(record_ids: list[int]) -> dict:
    """Delete only the rows inserted by the corresponding seed_database call."""
    if not record_ids or any(not isinstance(record_id, int) or isinstance(record_id, bool) for record_id in record_ids):
        return {"status": "error", "message": "A non-empty list of inserted record IDs is required."}
    try:
        placeholders = ",".join("?" for _ in record_ids)
        with closing(_connect()) as conn:
            cursor = conn.execute(
                f"DELETE FROM records WHERE id IN ({placeholders})",
                record_ids,
            )
            conn.commit()
        return {"status": "ok", "message": f"removed {cursor.rowcount} inserted rows"}
    except sqlite3.Error as exc:
        return {"status": "error", "message": str(exc)}


@server.tool()
def count_rows() -> dict:
    """Utility tool, not part of any saga - lets the demo script verify
    state before/after without needing raw SQL in the demo itself."""
    with closing(_connect()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {"status": "ok", "count": count}


if __name__ == "__main__":
    initialize_database()
    server.run()  # synchronous entry point; runs run_stdio_async() internally
