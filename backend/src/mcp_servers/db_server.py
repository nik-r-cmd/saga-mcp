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
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="db-server")

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("saga_demo.sqlite3")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return conn


@server.tool()
def seed_database(rows: list[str]) -> dict:
    """Insert a list of string payloads into the records table. Fails
    genuinely (returns status=error) if rows is empty or the DB is
    unreachable - used to demonstrate a real, unstaged saga failure."""
    if not rows:
        return {"status": "error", "message": "no rows provided to seed"}
    try:
        conn = _connect()
        conn.executemany(
            "INSERT INTO records (payload) VALUES (?)", [(r,) for r in rows]
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "message": f"inserted {len(rows)} rows"}
    except sqlite3.Error as exc:
        return {"status": "error", "message": str(exc)}


@server.tool()
def wipe_database() -> dict:
    """Delete all rows from the records table. Compensates
    seed_database."""
    try:
        conn = _connect()
        conn.execute("DELETE FROM records")
        conn.commit()
        conn.close()
        return {"status": "ok", "message": "table wiped"}
    except sqlite3.Error as exc:
        return {"status": "error", "message": str(exc)}


@server.tool()
def count_rows() -> dict:
    """Utility tool, not part of any saga - lets the demo script verify
    state before/after without needing raw SQL in the demo itself."""
    conn = _connect()
    count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    conn.close()
    return {"status": "ok", "count": count}


if __name__ == "__main__":
    server.run()  # synchronous entry point; runs run_stdio_async() internally
