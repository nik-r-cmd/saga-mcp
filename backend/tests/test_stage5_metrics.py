from __future__ import annotations

import os
import tempfile

from src.evaluation.metrics import RunResult, compute_report
from src.logging_utils.audit_log import AuditEntry, AuditLog, now_ms


def test_metrics_computed_correctly_from_mixed_results():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        audit_log = AuditLog(db_path=path)
        for latency in (10.0, 20.0, 30.0):
            audit_log.record(
                AuditEntry(
                    timestamp=now_ms(),
                    agent_id="planner",
                    tool_name="create_branch",
                    action_type="write",
                    decision="ALLOWED",
                    reason="ok",
                    latency_ms=latency,
                )
            )

        results = [
            RunResult(saga_status="rolled_back", should_succeed=False, latency_ms=10.0),
            RunResult(saga_status="dead_letter", should_succeed=False, latency_ms=15.0),
            RunResult(saga_status="completed", should_succeed=True, latency_ms=5.0),
            RunResult(saga_status="rolled_back", should_succeed=True, latency_ms=5.0),  # false positive
        ]

        report = compute_report(results, audit_log)

        assert report.total_runs == 4
        assert report.rollback_success_rate == 0.5  # 1 of 2 induced failures rolled back cleanly
        assert report.dead_letter_rate == 0.5
        assert report.false_positive_rate == 0.5  # 1 of 2 legitimate runs did not complete
        assert report.avg_latency_ms == 20.0  # mean of 10, 20, 30
        audit_log.close()
    finally:
        os.remove(path)
