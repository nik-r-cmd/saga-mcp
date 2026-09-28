"""
Computes the evaluation metrics your report/paper needs, by querying the
audit trail that SagaExecutor already writes to. Nothing here re-derives
saga logic - it strictly reads history, the same way the base paper's
own evaluation section works off logged decisions.

Metrics computed:
  - rollback_success_rate: of all sagas that failed, what fraction ended
    ROLLED_BACK (clean) vs DEAD_LETTER (compensation itself failed)
  - false_positive_rate: legitimate (should-succeed) runs that were
    wrongly blocked - requires the caller to pass which runs were
    "should have succeeded" ground truth
  - avg_latency_ms: mean per-step latency across all logged calls
  - dead_letter_rate: fraction of failed sagas that ended in DEAD_LETTER
"""

from __future__ import annotations

from dataclasses import dataclass

from src.logging_utils.audit_log import AuditLog


@dataclass
class RunResult:
    """One saga run's outcome, fed in by the evaluation script after
    calling SagaExecutor.run(). Kept separate from the audit trail itself
    because "was this supposed to succeed" is ground truth the harness
    knows and the executor does not."""

    saga_status: str  # SagaStatus.value
    should_succeed: bool  # ground truth: was this a legitimate run or an
    # induced-failure run?
    latency_ms: float


@dataclass
class EvaluationReport:
    total_runs: int
    rollback_success_rate: float  # of induced failures, fraction that
    # ended ROLLED_BACK rather than DEAD_LETTER
    dead_letter_rate: float
    false_positive_rate: float  # of legitimate runs, fraction that did
    # NOT end COMPLETED
    avg_latency_ms: float

    def as_table(self) -> str:
        return (
            f"{'Metric':<28}{'Value':>10}\n"
            f"{'-' * 38}\n"
            f"{'Total runs':<28}{self.total_runs:>10}\n"
            f"{'Rollback success rate':<28}{self.rollback_success_rate:>9.1%}\n"
            f"{'Dead-letter rate':<28}{self.dead_letter_rate:>9.1%}\n"
            f"{'False positive rate':<28}{self.false_positive_rate:>9.1%}\n"
            f"{'Avg per-step latency (ms)':<28}{self.avg_latency_ms:>10.2f}\n"
        )


def compute_report(results: list[RunResult], audit_log: AuditLog) -> EvaluationReport:
    induced_failures = [r for r in results if not r.should_succeed]
    legitimate_runs = [r for r in results if r.should_succeed]

    rolled_back = sum(1 for r in induced_failures if r.saga_status == "rolled_back")
    dead_lettered = sum(1 for r in induced_failures if r.saga_status == "dead_letter")

    rollback_success_rate = (
        rolled_back / len(induced_failures) if induced_failures else 0.0
    )
    dead_letter_rate = (
        dead_lettered / len(induced_failures) if induced_failures else 0.0
    )

    false_positives = sum(1 for r in legitimate_runs if r.saga_status != "completed")
    false_positive_rate = (
        false_positives / len(legitimate_runs) if legitimate_runs else 0.0
    )

    rows = audit_log.all_entries()
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0

    return EvaluationReport(
        total_runs=len(results),
        rollback_success_rate=rollback_success_rate,
        dead_letter_rate=dead_letter_rate,
        false_positive_rate=false_positive_rate,
        avg_latency_ms=avg_latency_ms,
    )
