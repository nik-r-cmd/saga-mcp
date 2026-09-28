"""
CompensationRegistry: the single source of truth for "how do I undo this
tool call?"

Addresses blind spot #2 directly. Registration is explicit and fails
loudly if a COMPENSABLE tool is registered without a compensating_tool -
there is no code path where the engine silently assumes a tool is
reversible.
"""

from __future__ import annotations

from src.saga.models import ActionCategory, ActionSpec


class UnregisteredToolError(Exception):
    """Raised when the engine encounters a tool call for a tool that was
    never registered. Fails loudly on purpose - an unregistered tool
    executing inside a saga is a configuration bug, not a runtime
    condition to paper over."""


class InvalidActionSpecError(Exception):
    """Raised at registration time, not at rollback time, if a
    COMPENSABLE tool is missing its compensating_tool."""


class CompensationRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        if spec.category == ActionCategory.COMPENSABLE and not spec.compensating_tool:
            raise InvalidActionSpecError(
                f"Tool '{spec.tool_name}' is marked COMPENSABLE but has no "
                "compensating_tool declared. Either provide one or mark it "
                "as ActionCategory.PIVOT."
            )
        self._specs[spec.tool_name] = spec

    def get(self, tool_name: str) -> ActionSpec:
        try:
            return self._specs[tool_name]
        except KeyError as exc:
            raise UnregisteredToolError(
                f"Tool '{tool_name}' was called but has no registered "
                "ActionSpec. Register every tool a saga can call before "
                "running it - unregistered tools cannot be safely undone."
            ) from exc

    def is_registered(self, tool_name: str) -> bool:
        return tool_name in self._specs

    def is_pivot(self, tool_name: str) -> bool:
        return self.get(tool_name).category == ActionCategory.PIVOT

    def tool_names(self) -> list[str]:
        return list(self._specs.keys())
