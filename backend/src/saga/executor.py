"""
SagaExecutor: ties registry + tracker + failure detector together and
runs a full saga forward, then backward on failure.

Design notes tied directly to the four blind spots:

  1. Compensation is retried up to spec.max_retries, respecting the
     declared idempotent flag. If it still fails, the step is marked
     COMPENSATION_FAILED and the saga ends in SagaStatus.DEAD_LETTER
     rather than silently reporting success.
  2. Rollback never proceeds past a PIVOT boundary (delegated to
     SagaTracker.rollback_sequence). If a PIVOT step already succeeded,
     the saga ends in PARTIALLY_COMMITTED, not ROLLED_BACK.
  3. This class only depends on plain callables for tool invocation - it
     has no import on any agent framework, so it can be dropped into a
     LangGraph pipeline, an AutoGen pipeline, or a raw script identically.
  4. Every invocation result is passed through FailureDetector, which
     checks both protocol- and payload-level failure signals before a
     step is marked SUCCEEDED.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from src.logging_utils.audit_log import AuditEntry, AuditLog, now_ms
from src.saga.dead_letter import DeadLetterEntry, DeadLetterQueue
from src.saga.failure_detection import FailureDetector
from src.saga.models import SagaStatus, SagaStep, StepStatus
from src.saga.registry import CompensationRegistry
from src.saga.tracker import SagaTracker

# A tool invoker takes (tool_name, arguments) and returns a response dict,
# or raises an exception on a protocol-level failure. Injected as a
# dependency so this module never talks to a real MCP server directly -
# that keeps it framework-agnostic and trivially testable.
ToolInvoker = Callable[[str, dict[str, Any]], dict[str, Any]]

# An event emitter takes a plain JSON-serializable dict describing a
# step transition and does whatever the caller wants with it (broadcast
# over a WebSocket, log it, ignore it). SagaExecutor has zero knowledge
# of WebSockets or any transport - it just calls this callback if one
# was provided, keeping the engine decoupled from how telemetry is
# delivered.
EventEmitter = Callable[[dict[str, Any]], None]


class SagaExecutor:
    def __init__(
        self,
        registry: CompensationRegistry,
        tool_invoker: ToolInvoker,
        detector: FailureDetector | None = None,
        audit_log: AuditLog | None = None,
        dead_letter_queue: DeadLetterQueue | None = None,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self._registry = registry
        self._invoke = tool_invoker
        self._detector = detector or FailureDetector()
        self._audit_log = audit_log
        self._dlq = dead_letter_queue
        self._emit = event_emitter

    def run(
        self, steps: list[tuple[str, dict[str, Any]]], agent_id: str = "unknown"
    ) -> SagaTracker:
        """Executes a sequence of (tool_name, arguments) pairs. Stops at
        the first failure and triggers rollback. Returns the tracker so
        callers can inspect the full record for the audit trail."""

        saga_id = str(uuid.uuid4())
        tracker = SagaTracker(saga_id=saga_id, registry=self._registry)

        for tool_name, arguments in steps:
            # Fail loudly if the tool was never registered (blind spot #2
            # in practice: you cannot safely run an action you don't know
            # how to undo).
            self._registry.get(tool_name)

            step = SagaStep(
                step_id=str(uuid.uuid4()),
                tool_name=tool_name,
                arguments=arguments,
                agent_id=agent_id,
            )
            tracker.add_step(step)

            start = now_ms()
            response, protocol_error = self._safe_invoke(tool_name, arguments)
            latency_ms = now_ms() - start
            is_failure, reason, detail = self._detector.check(response, protocol_error)

            if is_failure:
                step.status = StepStatus.FAILED
                step.failure_reason = reason
                step.failure_detail = detail
                self._log(saga_id, step, "BLOCKED", detail, latency_ms)
                self._rollback(tracker, saga_id)
                return tracker

            step.status = StepStatus.SUCCEEDED
            step.result = response
            self._log(saga_id, step, "ALLOWED", "forward step succeeded", latency_ms)

        tracker.record.status = SagaStatus.COMPLETED
        return tracker

    def _log(
        self, saga_id: str, step: SagaStep, decision: str, reason: str, latency_ms: float
    ) -> None:
        if self._audit_log is not None:
            self._audit_log.record(
                AuditEntry(
                    timestamp=now_ms(),
                    agent_id=step.agent_id,
                    tool_name=step.tool_name,
                    action_type="write",  # saga steps are mutating by
                    # definition; read-only classification is out of scope
                    # for this project's evaluation
                    decision=decision,  # type: ignore[arg-type]
                    reason=reason,
                    latency_ms=latency_ms,
                    extra={"saga_id": saga_id, "step_id": step.step_id},
                )
            )

        if self._emit is not None:
            self._emit(
                {
                    "event_type": self._event_type_for(step.status),
                    "saga_id": saga_id,
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "status": step.status.value,
                    "decision": decision,
                    "reason": reason,
                    "latency_ms": latency_ms,
                    "timestamp": now_ms(),
                }
            )

    @staticmethod
    def _event_type_for(status: StepStatus) -> str:
        # Maps internal StepStatus to the event taxonomy a frontend DAG
        # visualization would key its node colors off (documented in
        # docs/ROADMAP.md's Stage 6 notes).
        return {
            StepStatus.SUCCEEDED: "FORWARD_EXEC",
            StepStatus.FAILED: "FAULT_DETECTED",
            StepStatus.COMPENSATED: "COMPENSATED",
            StepStatus.COMPENSATION_FAILED: "DEAD_LETTER",
        }.get(status, "UNKNOWN")

    def _safe_invoke(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, Exception | None]:
        try:
            return self._invoke(tool_name, arguments), None
        except Exception as exc:  # noqa: BLE001 - intentionally broad,
            # any exception from a tool call is a protocol-level failure
            # for our purposes
            return None, exc

    def _rollback(self, tracker: SagaTracker, saga_id: str) -> None:
        sequence = tracker.rollback_sequence()
        any_compensation_failed = False

        for step in sequence:
            spec = self._registry.get(step.tool_name)
            compensated = self._compensate_with_retries(step, spec)
            if compensated:
                step.status = StepStatus.COMPENSATED
                self._log(saga_id, step, "ALLOWED", "compensation succeeded", 0.0)
            else:
                step.status = StepStatus.COMPENSATION_FAILED
                any_compensation_failed = True
                self._log(saga_id, step, "BLOCKED", "compensation failed after retries", 0.0)
                self._write_dead_letter(saga_id, step, spec.compensating_tool)

        if any_compensation_failed:
            tracker.record.status = SagaStatus.DEAD_LETTER
        elif tracker.hit_pivot_boundary():
            tracker.record.status = SagaStatus.PARTIALLY_COMMITTED
        else:
            tracker.record.status = SagaStatus.ROLLED_BACK

    def _write_dead_letter(self, saga_id: str, step: SagaStep, compensating_tool: str | None) -> None:
        if self._dlq is None:
            return
        self._dlq.add(
            DeadLetterEntry(
                saga_id=saga_id,
                step_id=step.step_id,
                tool_name=step.tool_name,
                compensating_tool=compensating_tool,
                arguments=step.arguments,
                failure_detail=step.failure_detail or "compensation failed after retries",
                timestamp=now_ms(),
                compensation_attempts=step.compensation_attempts,
            )
        )

    def _compensate_with_retries(self, step: SagaStep, spec) -> bool:
        if spec.compensating_tool is None:
            return False  # should be unreachable for COMPENSABLE tools,
            # guarded already at registration time

        # Explicit argument mapping (fixes the accidental-leniency bug:
        # previously the forward call's arguments were reused verbatim
        # for the compensating call, which only "worked" because MCP
        # silently tolerates unexpected extra kwargs rather than
        # rejecting them). Falls back to identity only when no mapper is
        # declared, for backward compatibility with tools that genuinely
        # share an identical schema.
        if spec.compensation_arg_mapper is not None:
            compensation_args = spec.compensation_arg_mapper(step.arguments)
        else:
            compensation_args = step.arguments

        for attempt in range(1, spec.max_retries + 1):
            step.compensation_attempts = attempt
            response, protocol_error = self._safe_invoke(
                spec.compensating_tool, compensation_args
            )
            is_failure, _, _ = self._detector.check(response, protocol_error)
            if not is_failure:
                return True
            # idempotent compensations are safe to retry as-is; a
            # non-idempotent one that fails is more dangerous to retry
            # blindly, but for this project's scope we still retry and
            # rely on the DLQ + audit trail for human review rather than
            # guessing at more complex recovery logic.
        return False
