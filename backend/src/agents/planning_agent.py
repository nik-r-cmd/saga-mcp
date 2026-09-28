"""
PlanningAgent: the actual "AI" in the pipeline. Takes a plain-English
task description, asks the LLM to produce an ordered tool-call plan, and
validates that plan against the CompensationRegistry before it's allowed
anywhere near SagaExecutor.

Testability note: this class depends on a plain LLMCallable
(system_prompt, user_prompt) -> raw_text, not on OllamaClient directly.
Tests inject a fake callable that returns canned LLM-style text
(including realistic messiness like markdown code fences) so the parsing
and validation logic is fully proven without a live model. Wire a real
OllamaClient.generate in production use - see scripts/run_saga_demo_llm.py.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from src.saga.registry import CompensationRegistry, UnregisteredToolError

LLMCallable = Callable[[str, str], str]


class PlanValidationError(Exception):
    """Raised when the LLM's plan is malformed or references a tool that
    isn't registered. Raised BEFORE anything reaches SagaExecutor - a bad
    plan should never get the chance to execute a real tool call."""


class PlanningAgent:
    def __init__(
        self,
        agent_id: str,
        registry: CompensationRegistry,
        llm_call: LLMCallable,
        tool_descriptions: dict[str, str],
    ) -> None:
        self.agent_id = agent_id
        self._registry = registry
        self._llm_call = llm_call
        self._tool_descriptions = tool_descriptions

    def plan(self, task_description: str) -> list[tuple[str, dict[str, Any]]]:
        system_prompt = self._build_system_prompt()
        raw = self._llm_call(system_prompt, task_description)
        parsed = self._parse_plan(raw)
        self._validate_plan(parsed)
        return [(step["tool"], step["arguments"]) for step in parsed]

    def _build_system_prompt(self) -> str:
        tool_lines = "\n".join(
            f"- {name}: {desc}" for name, desc in self._tool_descriptions.items()
        )
        return (
            "You are a planning agent for a DevOps automation pipeline. "
            "Given a task, output ONLY a JSON array (no prose, no markdown "
            "fences) of steps in the exact order they should execute. "
            "Each step is an object with exactly two keys: \"tool\" (a "
            "string, must be one of the tool names listed below) and "
            "\"arguments\" (an object matching that tool's expected "
            "parameters).\n\n"
            f"Available tools:\n{tool_lines}\n\n"
            'Example output: [{"tool": "create_branch", "arguments": '
            '{"branch_name": "feature/x"}}]'
        )

    @staticmethod
    def _parse_plan(raw: str) -> list[dict[str, Any]]:
        text = raw.strip()

        # LLMs frequently wrap JSON in markdown code fences even when
        # explicitly told not to - strip those before parsing rather than
        # failing on the first real-world model response.
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlanValidationError(
                f"LLM output was not valid JSON: {exc}. Raw output: {raw!r}"
            ) from exc

        if not isinstance(parsed, list):
            raise PlanValidationError(
                f"Expected a JSON array of steps, got {type(parsed).__name__}."
            )
        if len(parsed) == 0:
            raise PlanValidationError("LLM returned an empty plan.")

        for i, step in enumerate(parsed):
            if not isinstance(step, dict) or "tool" not in step or "arguments" not in step:
                raise PlanValidationError(
                    f"Step {i} is malformed, expected keys 'tool' and "
                    f"'arguments', got: {step!r}"
                )
            if not isinstance(step["arguments"], dict):
                raise PlanValidationError(
                    f"Step {i} 'arguments' must be an object, got: {step['arguments']!r}"
                )

        return parsed

    def _validate_plan(self, parsed: list[dict[str, Any]]) -> None:
        for i, step in enumerate(parsed):
            try:
                self._registry.get(step["tool"])
            except UnregisteredToolError as exc:
                raise PlanValidationError(
                    f"Step {i} references unregistered tool '{step['tool']}'. "
                    "The LLM hallucinated a tool that doesn't exist in this "
                    "pipeline. Refusing to execute this plan."
                ) from exc
