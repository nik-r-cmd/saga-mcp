"""
SagaTracker: records executed steps in order and computes the rollback
sequence when a failure occurs.

The rollback sequence is computed, not just "reverse the whole list" -
it stops at the most recent PIVOT step, because nothing before an
irreversible action can meaningfully be undone once that action has
fired (blind spot #2).
"""

from __future__ import annotations

from src.saga.models import ActionCategory, SagaRecord, SagaStep, StepStatus
from src.saga.registry import CompensationRegistry


class SagaTracker:
    def __init__(self, saga_id: str, registry: CompensationRegistry) -> None:
        self.record = SagaRecord(saga_id=saga_id)
        self._registry = registry

    def add_step(self, step: SagaStep) -> None:
        self.record.steps.append(step)

    def rollback_sequence(self) -> list[SagaStep]:
        """Walk completed steps in reverse, stopping at (and excluding)
        the most recent PIVOT step. Steps before that boundary are not
        eligible for compensation - the saga is partially committed from
        that point forward, by design."""

        sequence: list[SagaStep] = []
        for step in reversed(self.record.steps):
            if step.status != StepStatus.SUCCEEDED:
                continue
            spec = self._registry.get(step.tool_name)
            if spec.category == ActionCategory.PIVOT:
                break  # cannot roll back past an irreversible action
            sequence.append(step)
        return sequence

    def hit_pivot_boundary(self) -> bool:
        """True if any successfully executed step in this saga was a
        PIVOT action - meaning full rollback is not possible."""
        for step in self.record.steps:
            if step.status != StepStatus.SUCCEEDED:
                continue
            if self._registry.get(step.tool_name).category == ActionCategory.PIVOT:
                return True
        return False
