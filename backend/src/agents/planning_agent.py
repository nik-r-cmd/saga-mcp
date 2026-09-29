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
        tool_schemas: dict[str, dict[str, Any]] | None = None,
        max_plan_attempts: int = 2,
    ) -> None:
        self.agent_id = agent_id
        self._registry = registry
        self._llm_call = llm_call
        self._tool_descriptions = tool_descriptions
        self._tool_schemas = tool_schemas or {}
        self._max_plan_attempts = max(1, max_plan_attempts)

    def plan(self, task_description: str) -> list[tuple[str, dict[str, Any]]]:
        system_prompt = self._build_system_prompt()
        user_prompt = task_description
        last_error: PlanValidationError | None = None
        for attempt in range(self._max_plan_attempts):
            raw = self._llm_call(system_prompt, user_prompt)
            try:
                parsed = self._parse_plan(raw)
                self._validate_plan(parsed)
                return [(step["tool"], step["arguments"]) for step in parsed]
            except PlanValidationError as exc:
                last_error = exc
                if attempt + 1 < self._max_plan_attempts:
                    user_prompt = (
                        f"{task_description}\n\nYour previous plan was rejected: {exc}. "
                        "Return a corrected JSON array. Values must use native JSON types "
                        "from the tool schemas; do not encode arrays or objects as strings."
                    )
        raise last_error or PlanValidationError("The model did not produce a valid plan.")

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
            "parameters). Use native JSON value types exactly as shown in "
            "the input schema; arrays must be JSON arrays, not strings "
            "containing JSON. Do not add prose or extra keys.\n\n"
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
            tool_name = step["tool"]
            try:
                self._registry.get(tool_name)
            except UnregisteredToolError as exc:
                raise PlanValidationError(
                    f"Step {i} references unregistered tool '{tool_name}'. "
                    "The LLM hallucinated a tool that doesn't exist in this "
                    "pipeline. Refusing to execute this plan."
                ) from exc

            arguments = step["arguments"]
            schema = self._tool_schemas.get(tool_name)
            if not schema:
                continue

            properties = schema.get("properties", {})
            required = schema.get("required", [])
            additional_properties = schema.get("additionalProperties", True)
            for key, value in list(arguments.items()):
                property_schema = properties.get(key)
                if property_schema is None:
                    if additional_properties is False:
                        raise PlanValidationError(
                            f"Step {i} ({tool_name}) has unexpected argument '{key}'."
                        )
                    continue

                expected_type = property_schema.get("type")
                if expected_type in {"array", "object"} and isinstance(value, str):
                    try:
                        decoded = json.loads(value)
                    except json.JSONDecodeError:
                        decoded = value
                    if (expected_type == "array" and isinstance(decoded, list)) or (
                        expected_type == "object" and isinstance(decoded, dict)
                    ):
                        value = decoded
                        arguments[key] = value

                valid_type = {
                    "array": isinstance(value, list),
                    "object": isinstance(value, dict),
                    "string": isinstance(value, str),
                    "integer": isinstance(value, int) and not isinstance(value, bool),
                    "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                    "boolean": isinstance(value, bool),
                }.get(expected_type, True)
                if not valid_type:
                    raise PlanValidationError(
                        f"Step {i} ({tool_name}) argument '{key}' must be {expected_type}, "
                        f"got {type(value).__name__}."
                    )
                if expected_type == "array":
                    item_type = property_schema.get("items", {}).get("type")
                    if item_type == "string" and not all(isinstance(item, str) for item in value):
                        raise PlanValidationError(
                            f"Step {i} ({tool_name}) argument '{key}' must contain only strings."
                        )

            missing = [key for key in required if key not in arguments]
            if missing:
                raise PlanValidationError(
                    f"Step {i} ({tool_name}) is missing required arguments: {', '.join(missing)}."
                )
