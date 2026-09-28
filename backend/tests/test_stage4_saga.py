"""
Stage 4 (Saga engine) tests. Four of these tests map directly onto the
four blind spots identified before implementation - each one proves the
engine handles that case correctly, not just that the happy path works.
"""

from __future__ import annotations

import pytest

from src.saga.executor import SagaExecutor
from src.saga.failure_detection import FailureDetector
from src.saga.models import ActionCategory, ActionSpec, SagaStatus, StepStatus
from src.saga.registry import (
    CompensationRegistry,
    InvalidActionSpecError,
    UnregisteredToolError,
)


def make_registry() -> CompensationRegistry:
    reg = CompensationRegistry()
    reg.register(
        ActionSpec(
            tool_name="create_branch",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="delete_branch",
        )
    )
    reg.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    reg.register(
        ActionSpec(
            tool_name="seed_database",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="wipe_database",
        )
    )
    reg.register(ActionSpec(tool_name="wipe_database", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    reg.register(ActionSpec(tool_name="noop", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    reg.register(ActionSpec(tool_name="send_email", category=ActionCategory.PIVOT))
    reg.register(ActionSpec(tool_name="run_build", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    return reg


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------

def test_all_steps_succeed_no_rollback_needed():
    registry = make_registry()

    def invoker(tool_name, args):
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    tracker = executor.run([("create_branch", {}), ("seed_database", {})])

    assert tracker.record.status == SagaStatus.COMPLETED
    assert all(s.status == StepStatus.SUCCEEDED for s in tracker.record.steps)


# ---------------------------------------------------------------------
# Blind spot #2: non-reversible (PIVOT) actions
# ---------------------------------------------------------------------

def test_registry_rejects_compensable_tool_without_compensation():
    registry = CompensationRegistry()
    with pytest.raises(InvalidActionSpecError):
        registry.register(ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE))


def test_unregistered_tool_raises_loudly():
    registry = make_registry()

    def invoker(tool_name, args):
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    with pytest.raises(UnregisteredToolError):
        executor.run([("deploy_to_prod_unregistered", {})])


def test_pivot_step_blocks_further_rollback():
    registry = make_registry()

    def invoker(tool_name, args):
        if tool_name == "run_build":
            return {"status": "error", "message": "build failed"}
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    # create_branch (compensable) -> send_email (PIVOT) -> run_build (fails)
    tracker = executor.run([("create_branch", {}), ("send_email", {}), ("run_build", {})])

    assert tracker.record.status == SagaStatus.PARTIALLY_COMMITTED
    # create_branch must NOT have been compensated - it's before the pivot
    create_step = tracker.record.steps[0]
    assert create_step.status == StepStatus.SUCCEEDED


# ---------------------------------------------------------------------
# Blind spot #1: compensation itself can fail
# ---------------------------------------------------------------------

def test_compensation_failure_leads_to_dead_letter():
    registry = make_registry()

    def invoker(tool_name, args):
        if tool_name == "seed_database":
            return {"status": "error", "message": "disk full"}
        if tool_name == "delete_branch":
            return {"status": "error", "message": "git server unreachable"}
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    tracker = executor.run([("create_branch", {}), ("seed_database", {})])

    assert tracker.record.status == SagaStatus.DEAD_LETTER
    branch_step = tracker.record.steps[0]
    assert branch_step.status == StepStatus.COMPENSATION_FAILED
    assert branch_step.compensation_attempts == 3  # exhausted retries


def test_successful_rollback_when_all_compensations_succeed():
    registry = make_registry()

    def invoker(tool_name, args):
        if tool_name == "seed_database":
            return {"status": "error", "message": "disk full"}
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    tracker = executor.run([("create_branch", {}), ("seed_database", {})])

    assert tracker.record.status == SagaStatus.ROLLED_BACK
    assert tracker.record.steps[0].status == StepStatus.COMPENSATED


# ---------------------------------------------------------------------
# Blind spot #4: semantic vs protocol failure detection
# ---------------------------------------------------------------------

def test_detects_semantic_failure_inside_successful_envelope():
    detector = FailureDetector()
    is_failure, reason, detail = detector.check({"status": "error", "message": "resource locked"})
    assert is_failure is True
    assert detail == "resource locked"


def test_detects_success_false_variant():
    detector = FailureDetector()
    is_failure, _, detail = detector.check({"success": False, "reason": "quota exceeded"})
    assert is_failure is True
    assert detail == "quota exceeded"


def test_detects_protocol_level_exception():
    detector = FailureDetector()
    is_failure, reason, _ = detector.check(None, protocol_error=TimeoutError("mcp call timed out"))
    assert is_failure is True
    assert reason.value == "protocol_error"


def test_healthy_response_is_not_a_failure():
    detector = FailureDetector()
    is_failure, _, _ = detector.check({"status": "ok", "data": {"id": 1}})
    assert is_failure is False


# ---------------------------------------------------------------------
# Argument mapping: fixes the bug where compensating calls silently
# reused forward-call arguments even when the compensating tool has a
# completely different schema
# ---------------------------------------------------------------------

def test_compensation_uses_mapped_arguments_not_forward_arguments():
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(
            tool_name="commit_file",
            category=ActionCategory.COMPENSABLE,
            compensating_tool="revert_last_commit",
            compensation_arg_mapper=lambda args: {},  # revert takes no args
        )
    )
    registry.register(ActionSpec(tool_name="revert_last_commit", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    registry.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    registry.register(ActionSpec(tool_name="noop", category=ActionCategory.PIVOT))

    calls_seen = []

    def invoker(tool_name, args):
        calls_seen.append((tool_name, args))
        if tool_name == "seed_database":
            return {"status": "error", "message": "disk full"}
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    tracker = executor.run(
        [
            ("commit_file", {"path": "x", "content": "y", "message": "z"}),  # succeeds
            ("seed_database", {}),  # fails, triggers rollback of commit_file
        ]
    )

    assert tracker.record.status == SagaStatus.ROLLED_BACK
    revert_call = next(c for c in calls_seen if c[0] == "revert_last_commit")
    # This is the actual bug fix: revert_last_commit must be called with
    # {} (per the mapper), NOT with commit_file's {path, content, message}
    assert revert_call[1] == {}


def test_no_mapper_falls_back_to_identity_for_backward_compatibility():
    registry = CompensationRegistry()
    registry.register(
        ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch")
    )
    registry.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    registry.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="noop"))
    registry.register(ActionSpec(tool_name="noop", category=ActionCategory.PIVOT))

    calls_seen = []

    def invoker(tool_name, args):
        calls_seen.append((tool_name, args))
        if tool_name == "seed_database":
            return {"status": "error", "message": "disk full"}
        return {"status": "ok"}

    executor = SagaExecutor(registry, invoker)
    executor.run(
        [
            ("create_branch", {"branch_name": "feature/x"}),  # succeeds
            ("seed_database", {}),  # fails, triggers rollback of create_branch
        ]
    )

    delete_call = next(c for c in calls_seen if c[0] == "delete_branch")
    assert delete_call[1] == {"branch_name": "feature/x"}
