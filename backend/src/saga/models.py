"""
Core data models for the Saga engine.

Every design decision here maps directly to one of the four blind spots
identified before implementation:

  1. Compensation can fail -> StepStatus.COMPENSATION_FAILED and
     SagaStatus.DEAD_LETTER exist as first-class states, not error codes
     bolted on later.
  2. Not every action is reversible -> ActionCategory distinguishes
     COMPENSABLE from PIVOT at the point of registration, not at rollback
     time.
  3. Enforcement lives at the MCP boundary, not inside the agent
     framework -> nothing in this module imports LangGraph/AutoGen/etc.
     It only knows about tool names and JSON-serializable payloads, so it
     stays framework-agnostic by construction.
  4. Failures can be silent (HTTP/JSON-RPC 200 wrapping a semantic error)
     -> FailureReason distinguishes protocol-level and semantic-level
     failures so the detector (failure_detection.py) can report which
     layer caught the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ActionCategory(str, Enum):
    """Classification declared once, at registration time, for every tool
    a saga can call. This is what prevents the engine from ever *assuming*
    an action is reversible."""

    COMPENSABLE = "compensable"  # has a registered, meaningful undo
    PIVOT = "pivot"  # irreversible once executed (e.g. send_email,
    # charge_payment) - reaching a PIVOT step ends rollback eligibility
    # for every step before it


class StepStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"  # undo ran successfully
    COMPENSATION_FAILED = "compensation_failed"  # undo was attempted and
    # did not succeed after retries - this step is now stuck, not silently
    # dropped


class SagaStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"  # every step succeeded, nothing to undo
    ROLLED_BACK = "rolled_back"  # a failure occurred and every eligible
    # step was successfully compensated
    PARTIALLY_COMMITTED = "partially_committed"  # a PIVOT step already
    # executed before the failure; rollback stopped at that boundary by
    # design, not by accident
    DEAD_LETTER = "dead_letter"  # at least one compensation itself failed
    # after retries and needs a human


class FailureReason(str, Enum):
    PROTOCOL_ERROR = "protocol_error"  # JSON-RPC / MCP-level error object
    SEMANTIC_ERROR = "semantic_error"  # call "succeeded" at the protocol
    # level but the payload body reports failure (e.g. {"status": "error"})
    TIMEOUT = "timeout"
    NONE = "none"


@dataclass
class ActionSpec:
    """Declares how to undo a given forward tool call. Registered once
    per tool in the CompensationRegistry, never inferred at runtime."""

    tool_name: str
    category: ActionCategory
    compensating_tool: str | None = None  # required if COMPENSABLE,
    # ignored if PIVOT
    idempotent: bool = True  # can the compensating call be safely retried
    # more than once without side effects compounding? Declared explicitly
    # rather than assumed - a false assumption here is exactly how you get
    # duplicate refunds / double-deletes in real systems.
    max_retries: int = 3
    compensation_arg_mapper: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # Maps the FORWARD call's arguments to the arguments the COMPENSATING
    # call needs. Explicit by design: the previous behavior (silently
    # reusing the forward call's arguments as-is) only "worked" because
    # MCP tolerates unexpected extra kwargs rather than rejecting them -
    # that is accidental leniency, not a correctness guarantee, and it
    # breaks the moment a compensating tool's schema genuinely differs
    # from the forward tool's schema. If None, defaults to identity
    # (reuse forward arguments unchanged) for backward compatibility -
    # only safe when the forward and compensating tool share an
    # identical argument schema (e.g. create_branch/delete_branch both
    # take branch_name). Any compensating tool with a DIFFERENT schema
    # (e.g. revert_last_commit takes no arguments at all) MUST declare
    # an explicit mapper, typically `lambda args: {}`.


@dataclass
class SagaStep:
    """One executed forward action within a saga, plus everything needed
    to compensate it later."""

    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    agent_id: str = "unknown"  # which agent proposed this step; set by
    # SagaExecutor.run() when called with an agent_id, used for audit trail
    result: dict[str, Any] | None = None
    status: StepStatus = StepStatus.PENDING
    compensation_attempts: int = 0
    failure_reason: FailureReason = FailureReason.NONE
    failure_detail: str = ""


@dataclass
class SagaRecord:
    """The full execution graph for one saga run, in order. Rollback
    always walks this list in reverse from the point of failure."""

    saga_id: str
    steps: list[SagaStep] = field(default_factory=list)
    status: SagaStatus = SagaStatus.RUNNING
