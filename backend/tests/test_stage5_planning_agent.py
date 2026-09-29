"""
PlanningAgent tests. All LLM calls are faked here on purpose - these
tests prove the parsing and validation logic is correct independent of
whether a real model is running. The real-model confirmation happens
separately, on a machine with Ollama installed (see scripts/run_saga_demo_llm.py).
"""

from __future__ import annotations

import pytest

from src.agents.planning_agent import PlanningAgent, PlanValidationError
from src.saga.models import ActionCategory, ActionSpec
from src.saga.registry import CompensationRegistry


def make_registry() -> CompensationRegistry:
    reg = CompensationRegistry()
    reg.register(ActionSpec(tool_name="create_branch", category=ActionCategory.COMPENSABLE, compensating_tool="delete_branch"))
    reg.register(ActionSpec(tool_name="delete_branch", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    reg.register(ActionSpec(tool_name="seed_database", category=ActionCategory.COMPENSABLE, compensating_tool="wipe_database"))
    reg.register(ActionSpec(tool_name="wipe_database", category=ActionCategory.COMPENSABLE, compensating_tool="_noop"))
    reg.register(ActionSpec(tool_name="_noop", category=ActionCategory.PIVOT))
    return reg


TOOL_DESCRIPTIONS = {
    "create_branch": "Create a git branch. args: branch_name",
    "seed_database": "Insert rows into the database. args: rows (list of strings)",
}


def test_clean_json_plan_parses_correctly():
    def fake_llm(system, user):
        return '[{"tool": "create_branch", "arguments": {"branch_name": "feature/x"}}]'

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    plan = agent.plan("create a feature branch")

    assert plan == [("create_branch", {"branch_name": "feature/x"})]


def test_plan_wrapped_in_markdown_fence_is_still_parsed():
    def fake_llm(system, user):
        return '```json\n[{"tool": "create_branch", "arguments": {"branch_name": "x"}}]\n```'

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    plan = agent.plan("create a branch")

    assert plan == [("create_branch", {"branch_name": "x"})]


def test_multi_step_plan_preserves_order():
    def fake_llm(system, user):
        return (
            '[{"tool": "create_branch", "arguments": {"branch_name": "x"}}, '
            '{"tool": "seed_database", "arguments": {"rows": ["a", "b"]}}]'
        )

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    plan = agent.plan("create a branch then seed data")

    assert plan == [
        ("create_branch", {"branch_name": "x"}),
        ("seed_database", {"rows": ["a", "b"]}),
    ]


def test_invalid_json_raises_plan_validation_error():
    def fake_llm(system, user):
        return "sure! here is your plan: create a branch"  # not JSON at all

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    with pytest.raises(PlanValidationError, match="not valid JSON"):
        agent.plan("create a branch")


def test_hallucinated_tool_is_rejected():
    def fake_llm(system, user):
        return '[{"tool": "deploy_to_production", "arguments": {}}]'

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    with pytest.raises(PlanValidationError, match="unregistered tool"):
        agent.plan("deploy this")


def test_empty_plan_is_rejected():
    def fake_llm(system, user):
        return "[]"

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    with pytest.raises(PlanValidationError, match="empty plan"):
        agent.plan("do nothing")


def test_malformed_step_missing_arguments_key_is_rejected():
    def fake_llm(system, user):
        return '[{"tool": "create_branch"}]'

    agent = PlanningAgent("planner", make_registry(), fake_llm, TOOL_DESCRIPTIONS)
    with pytest.raises(PlanValidationError, match="malformed"):
        agent.plan("create a branch")


def test_json_encoded_array_argument_is_normalized_from_live_model_output():
    def fake_llm(system, user):
        return '[{"tool":"seed_database","arguments":{"rows":"[\\"alpha-01\\",\\"beta-01\\"]"}}]'

    agent = PlanningAgent(
        "planner",
        make_registry(),
        fake_llm,
        TOOL_DESCRIPTIONS,
        tool_schemas={
            "seed_database": {
                "type": "object",
                "required": ["rows"],
                "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
            }
        },
    )

    assert agent.plan("seed rows") == [
        ("seed_database", {"rows": ["alpha-01", "beta-01"]})
    ]


def test_schema_type_violation_retries_once_with_validation_feedback():
    responses = iter([
        '[{"tool":"seed_database","arguments":{"rows":7}}]',
        '[{"tool":"seed_database","arguments":{"rows":["a","b"]}}]',
    ])
    prompts = []

    def fake_llm(system, user):
        prompts.append(user)
        return next(responses)

    agent = PlanningAgent(
        "planner",
        make_registry(),
        fake_llm,
        TOOL_DESCRIPTIONS,
        tool_schemas={
            "seed_database": {
                "required": ["rows"],
                "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
            }
        },
    )

    assert agent.plan("seed two rows") == [("seed_database", {"rows": ["a", "b"]})]
    assert len(prompts) == 2
    assert "must be array" in prompts[1]
