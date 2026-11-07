from __future__ import annotations

import sqlite3

from src.mcp_servers import db_server


def test_database_is_created_with_missing_parent_directories(monkeypatch, tmp_path):
    database_path = tmp_path / "new-folder" / "nested" / "demo.sqlite3"
    monkeypatch.setattr(db_server, "DB_PATH", database_path)

    assert not database_path.exists()
    db_server.initialize_database()

    assert database_path.is_file()
    assert db_server.count_rows() == {"status": "ok", "count": 0}
    assert db_server.seed_database(["first", "second"])["status"] == "ok"
    assert db_server.count_rows() == {"status": "ok", "count": 2}


def test_wipe_database_removes_only_the_requested_rows(monkeypatch, tmp_path):
    database_path = tmp_path / "demo.sqlite3"
    monkeypatch.setattr(db_server, "DB_PATH", database_path)

    seeded = db_server.seed_database(["keep", "remove"])
    assert seeded["status"] == "ok"

    removed = db_server.wipe_database([seeded["record_ids"][1]])

    assert removed == {"status": "ok", "message": "removed 1 inserted rows"}
    assert db_server.count_rows() == {"status": "ok", "count": 1}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT payload FROM records").fetchall() == [("keep",)]
