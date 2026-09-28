from __future__ import annotations

import os
import tempfile

from src.saga.dead_letter import DeadLetterQueue
from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec, SagaStatus
from src.saga.registry import CompensationRegistry


def test_dead_letter_entry_created_when_compensation_fails():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        dlq = DeadLetterQueue(path=path)

        registry = CompensationRegistry()
        registry.register(
            ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch")
        )
        registry.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
        registry.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
        registry.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))

        def invoker(tool, args):
            if tool == "seed_database":
                return {"status": "error", "message": "disk full"}
            if tool == "delete_branch":
                return {"status": "error", "message": "remote unreachable"}  # compensation itself fails
            return {"status": "ok"}

        executor = SagaExecutor(registry, invoker, dead_letter_queue=dlq)
        tracker = executor.run(
            [("create_branch", {"branch_name": "x"}), ("seed_database", {})]
        )

        assert tracker.record.status == SagaStatus.DEAD_LETTER

        unresolved = dlq.list_unresolved()
        assert len(unresolved) == 1
        assert unresolved[0]["tool_name"] == "create_branch"
        assert unresolved[0]["compensating_tool"] == "delete_branch"
        assert unresolved[0]["resolved"] == 0
        assert dlq.count_unresolved() == 1

        dlq.resolve(unresolved[0]["id"])
        assert dlq.count_unresolved() == 0
        dlq.close()
    finally:
        os.remove(path)


def test_no_dead_letter_entries_on_clean_rollback():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        dlq = DeadLetterQueue(path=path)

        registry = CompensationRegistry()
        registry.register(
            ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch")
        )
        registry.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
        registry.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
        registry.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))

        def invoker(tool, args):
            if tool == "seed_database":
                return {"status": "error", "message": "disk full"}
            return {"status": "ok"}  # delete_branch succeeds this time

        executor = SagaExecutor(registry, invoker, dead_letter_queue=dlq)
        tracker = executor.run(
            [("create_branch", {"branch_name": "x"}), ("seed_database", {})]
        )

        assert tracker.record.status == SagaStatus.ROLLED_BACK
        assert dlq.count_unresolved() == 0
        dlq.close()
    finally:
        os.remove(path)
