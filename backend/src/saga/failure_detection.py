"""
FailureDetector: catches failures at two layers.

Addresses blind spot #4. A naive wrapper only checks whether the MCP
call itself raised/returned a JSON-RPC error object. That misses tools
that return a normal, successful envelope containing a semantic failure
inside the payload body, e.g.:

    {"status": "error", "message": "resource locked"}
    {"success": false, "reason": "quota exceeded"}

Both layers are checked independently, and the reason is tagged so the
audit trail can distinguish "the protocol failed" from "the tool told us
it failed."
"""

from __future__ import annotations

from typing import Any

from src.saga.models import FailureReason

# Common keys tools use to signal a semantic (payload-level) failure.
# Extend this list as real tool servers are integrated in later stages.
_SEMANTIC_ERROR_KEYS = ("status", "success", "ok", "error")


class FailureDetector:
    def check(
        self, response: dict[str, Any] | None, protocol_error: Exception | None = None
    ) -> tuple[bool, FailureReason, str]:
        """Returns (is_failure, reason, detail)."""

        if protocol_error is not None:
            return True, FailureReason.PROTOCOL_ERROR, str(protocol_error)

        if response is None:
            return True, FailureReason.PROTOCOL_ERROR, "empty response"

        semantic_failure, detail = self._check_semantic(response)
        if semantic_failure:
            return True, FailureReason.SEMANTIC_ERROR, detail

        return False, FailureReason.NONE, ""

    @staticmethod
    def _check_semantic(response: dict[str, Any]) -> tuple[bool, str]:
        status = response.get("status")
        if isinstance(status, str) and status.lower() == "error":
            return True, response.get("message", "status=error")

        success = response.get("success")
        if success is False:
            return True, response.get("reason", "success=false")

        ok = response.get("ok")
        if ok is False:
            return True, response.get("message", "ok=false")

        error_field = response.get("error")
        if error_field:
            return True, str(error_field)

        return False, ""
