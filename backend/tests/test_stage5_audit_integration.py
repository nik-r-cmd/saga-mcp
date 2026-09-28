"""
Proves SagaExecutor actually writes to the AuditLog, not just that the
plumbing compiles. This is what Stage 5's evaluation harness reads from,
so it needs to be correct before anything is built on top of it.
"""

from __future__ import annotations

import os
import tempfile

from src.logging_utils.audit_log import AuditLog
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry


def make_registry() -> CompensationRegistry:
    reg = CompensationRegistry()
    reg.register(ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch"))
    reg.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    reg.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))
    return reg


def test_successful_step_logs_allowed_entry():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        audit_log = AuditLog(db_path=path)

        def invoker(tool, args):
            return {"status": "ok"}

        executor = SagaExecutor(make_registry(), invoker, audit_log=audit_log)
        executor.run([("create_branch", {"branch_name": "x"})], agent_id="planner_1")

        rows = audit_log.all_entries()
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "planner_1"
        assert rows[0]["decision"] == "ALLOWED"
        assert rows[0]["tool_name"] == "create_branch"
        audit_log.close()
    finally:
        os.remove(path)


def test_failed_step_and_compensation_both_logged():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        audit_log = AuditLog(db_path=path)

        def invoker(tool, args):
            if tool == "create_branch":
                return {"status": "ok"}
            if tool == "seed_fail":
                return {"status": "error", "message": "boom"}
            return {"status": "ok"}

        reg = make_registry()
        reg.register(ActionSpec(tool_name="seed_fail", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))

        executor = SagaExecutor(reg, invoker, audit_log=audit_log)
        executor.run(
            [("create_branch", {"branch_name": "x"}), ("seed_fail", {})],
            agent_id="planner_2",
        )

        rows = audit_log.all_entries()
        decisions = [r["decision"] for r in rows]
        # Expect: create_branch ALLOWED, seed_fail BLOCKED, create_branch compensation ALLOWED
        assert decisions.count("ALLOWED") == 2
        assert decisions.count("BLOCKED") == 1
        audit_log.close()
    finally:
        os.remove(path)
