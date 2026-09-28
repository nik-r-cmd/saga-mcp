from __future__ import annotations

from src.saga.executor import SagaExecutor
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry


def make_registry() -> CompensationRegistry:
    reg = CompensationRegistry()
    reg.register(ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch"))
    reg.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    reg.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    reg.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))
    return reg


def test_forward_success_emits_forward_exec_event():
    events = []

    def invoker(tool, args):
        return {"status": "ok"}

    executor = SagaExecutor(make_registry(), invoker, event_emitter=events.append)
    executor.run([("create_branch", {"branch_name": "x"})])

    assert len(events) == 1
    assert events[0]["event_type"] == "FORWARD_EXEC"
    assert events[0]["tool_name"] == "create_branch"
    assert "saga_id" in events[0]
    assert "timestamp" in events[0]


def test_failure_and_rollback_emit_correct_event_sequence():
    events = []

    def invoker(tool, args):
        if tool == "seed_database":
            return {"status": "error", "message": "disk full"}
        return {"status": "ok"}

    executor = SagaExecutor(make_registry(), invoker, event_emitter=events.append)
    executor.run([("create_branch", {"branch_name": "x"}), ("seed_database", {})])

    event_types = [e["event_type"] for e in events]
    assert event_types == ["FORWARD_EXEC", "FAULT_DETECTED", "COMPENSATED"]


def test_compensation_failure_emits_dead_letter_event():
    events = []

    def invoker(tool, args):
        if tool == "seed_database":
            return {"status": "error", "message": "disk full"}
        if tool == "delete_branch":
            return {"status": "error", "message": "still failing"}
        return {"status": "ok"}

    executor = SagaExecutor(make_registry(), invoker, event_emitter=events.append)
    executor.run([("create_branch", {"branch_name": "x"}), ("seed_database", {})])

    event_types = [e["event_type"] for e in events]
    assert event_types == ["FORWARD_EXEC", "FAULT_DETECTED", "DEAD_LETTER"]


def test_no_emitter_does_not_break_execution():
    """Executor must work identically with event_emitter=None (default) -
    telemetry is strictly optional, never a hard dependency."""
    def invoker(tool, args):
        return {"status": "ok"}

    executor = SagaExecutor(make_registry(), invoker)  # no emitter passed
    tracker = executor.run([("create_branch", {"branch_name": "x"})])
    assert tracker.record.steps[0].status.value == "succeeded"
