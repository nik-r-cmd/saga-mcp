"""
AutoPairSuggester: given a list of discovered tools, suggests (a) which
tool likely reverses which other tool, and (b) whether a tool looks
irreversible (PIVOT).

This is explicitly a SUGGESTION engine, not a decision engine - nothing
here writes to the CompensationRegistry directly. A human approves or
edits every suggestion before it becomes real (see api/main.py
/registry/approve). This is a deliberate design choice, not a
limitation: auto-approving reversibility guesses is exactly the failure
mode this project's own saga engine was built to guard against.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.discovery.tool_discovery import DiscoveredTool

# Verb pairs that commonly indicate a forward/inverse relationship.
# Ordered (forward_verb, inverse_verb).
_INVERSE_VERB_PAIRS = [
    ("create", "delete"),
    ("add", "remove"),
    ("seed", "wipe"),
    ("insert", "delete"),
    ("open", "close"),
    ("start", "stop"),
    ("lock", "unlock"),
    ("commit", "revert"),
    ("provision", "deprovision"),
    ("allocate", "deallocate"),
    ("grant", "revoke"),
]

# Verbs that strongly suggest an action cannot be meaningfully undone.
_IRREVERSIBLE_VERBS = (
    "send", "post", "publish", "notify", "email", "charge", "pay",
    "message", "broadcast", "deploy", "release",
)


@dataclass
class PairingSuggestion:
    tool_name: str
    suggested_category: str  # "compensable" | "pivot"
    suggested_compensating_tool: str | None
    confidence: str  # "high" | "low" - low confidence should be visually
    # flagged in the UI as "needs review", not just presented the same
    # as a high-confidence match


class AutoPairSuggester:
    def suggest(self, tools: list[DiscoveredTool]) -> list[PairingSuggestion]:
        names = {t.name for t in tools}
        suggestions: list[PairingSuggestion] = []

        for tool in tools:
            irreversible_match = self._matches_irreversible_verb(tool.name)
            if irreversible_match:
                suggestions.append(
                    PairingSuggestion(
                        tool_name=tool.name,
                        suggested_category="pivot",
                        suggested_compensating_tool=None,
                        confidence="high",
                    )
                )
                continue

            inverse = self._find_inverse(tool.name, names)
            if inverse:
                suggestions.append(
                    PairingSuggestion(
                        tool_name=tool.name,
                        suggested_category="compensable",
                        suggested_compensating_tool=inverse,
                        confidence="high",
                    )
                )
            else:
                # No confident match either way - surface it as
                # low-confidence rather than silently guessing.
                # Defaulting to PIVOT is the SAFE default: an
                # unrecognized tool is treated as irreversible until a
                # human says otherwise, never the other way around.
                suggestions.append(
                    PairingSuggestion(
                        tool_name=tool.name,
                        suggested_category="pivot",
                        suggested_compensating_tool=None,
                        confidence="low",
                    )
                )

        return suggestions

    @staticmethod
    def _matches_irreversible_verb(tool_name: str) -> bool:
        lowered = tool_name.lower()
        return any(verb in lowered for verb in _IRREVERSIBLE_VERBS)

    @staticmethod
    def _find_inverse(tool_name: str, all_names: set[str]) -> str | None:
        lowered = tool_name.lower()
        others = {n for n in all_names if n.lower() != lowered}

        for forward_verb, inverse_verb in _INVERSE_VERB_PAIRS:
            if forward_verb in lowered:
                match = next((n for n in others if inverse_verb in n.lower()), None)
                if match:
                    return match
            if inverse_verb in lowered:
                match = next((n for n in others if forward_verb in n.lower()), None)
                if match:
                    return match
        return None
