from __future__ import annotations

import json

import pytest

from src.discovery.repo_scanner import scan_repo


def test_finds_mcp_json_at_repo_root(tmp_path):
    config = {
        "mcpServers": {
            "git": {"command": "python", "args": ["-m", "src.mcp_servers.git_server", "/tmp/repo"]},
            "db": {"command": "python", "args": ["-m", "src.mcp_servers.db_server", "/tmp/db.sqlite3"]},
        }
    }
    (tmp_path / "mcp.json").write_text(json.dumps(config))

    results = scan_repo(tmp_path)
    names = {r.server_name for r in results}

    assert names == {"git", "db"}
    git_result = next(r for r in results if r.server_name == "git")
    assert git_result.command == "python"
    assert git_result.args == ["-m", "src.mcp_servers.git_server", "/tmp/repo"]
    assert git_result.source_file == "mcp.json"


def test_finds_config_in_dot_cursor_directory(tmp_path):
    (tmp_path / ".cursor").mkdir()
    config = {"mcpServers": {"custom": {"command": "node", "args": ["server.js"]}}}
    (tmp_path / ".cursor" / "mcp.json").write_text(json.dumps(config))

    results = scan_repo(tmp_path)
    assert len(results) == 1
    assert results[0].server_name == "custom"
    assert results[0].source_file == str(pytest.importorskip("pathlib").Path(".cursor") / "mcp.json")


def test_malformed_json_is_skipped_not_crashed(tmp_path):
    (tmp_path / "mcp.json").write_text("{ this is not valid json")
    results = scan_repo(tmp_path)
    assert results == []


def test_skips_node_modules_and_git_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"should_not_appear": {"command": "x", "args": []}}})
    )
    results = scan_repo(tmp_path)
    assert results == []


def test_nonexistent_repo_path_raises():
    with pytest.raises(FileNotFoundError):
        scan_repo("/this/path/does/not/exist/at/all")


def test_no_config_file_returns_empty_list(tmp_path):
    results = scan_repo(tmp_path)
    assert results == []
