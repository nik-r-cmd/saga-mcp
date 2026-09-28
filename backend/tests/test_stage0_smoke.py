"""
Stage 0 acceptance test. If this file passes, the scaffold is sound:
- src package imports cleanly
- config loads without error
- audit log can be created and written to
- BaseAgent's contract is importable (even though no concrete agent
  exists yet)

Every later stage adds its own test_stageN_*.py file. Nothing gets built
without a test proving the previous stage still works.
"""

from __future__ import annotations

import os
import tempfile

from src.agents.base_agent import AgentRequest, PrivilegeLevel
from src.logging_utils.audit_log import AuditEntry, AuditLog, now_ms


def test_config_imports() -> None:
    from src import config

    assert config.OLLAMA.model
    assert config.LOG_DIR.exists()


def test_privilege_level_enum() -> None:
    assert PrivilegeLevel.LOW.value == "low"
    assert PrivilegeLevel.HIGH.value == "high"


def test_agent_request_tracks_origin() -> None:
    req = AgentRequest(
        task_description="search issues",
        requested_by="support_agent",
        origin_agent_id="support_agent",
    )
    assert req.origin_agent_id == "support_agent"


def test_audit_log_write_and_read() -> None:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        log = AuditLog(db_path=path)
        entry = AuditEntry(
            timestamp=now_ms(),
            agent_id="support_agent",
            tool_name="list_issues",
            action_type="read",
            decision="ALLOWED",
            reason="in scope",
        )
        log.record(entry)

        rows = log.all_entries()
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "support_agent"
        assert rows[0]["decision"] == "ALLOWED"
        log.close()
    finally:
        os.remove(path)
