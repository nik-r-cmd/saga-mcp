from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.discovery.registry_store import ApprovedPairing, RegistryStore
from src.saga.models import ActionCategory


@pytest.fixture()
def store():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.remove(path)  # let RegistryStore create it fresh
    s = RegistryStore(path=path)
    yield s
    if os.path.exists(path):
        os.remove(path)


def test_approve_and_list(store):
    store.approve(ApprovedPairing(tool_name="create_branch", category="compensable", compensating_tool="delete_branch"))
    approvals = store.list_approvals()
    assert len(approvals) == 1
    assert approvals[0].tool_name == "create_branch"


def test_compensable_without_compensating_tool_rejected(store):
    with pytest.raises(ValueError, match="Cannot approve"):
        store.approve(ApprovedPairing(tool_name="seed_database", category="compensable"))


def test_re_approving_same_tool_updates_not_duplicates(store):
    store.approve(ApprovedPairing(tool_name="create_branch", category="compensable", compensating_tool="delete_branch"))
    store.approve(ApprovedPairing(tool_name="create_branch", category="pivot"))  # re-approved differently
    approvals = store.list_approvals()
    assert len(approvals) == 1
    assert approvals[0].category == "pivot"


def test_reject_removes_approval(store):
    store.approve(ApprovedPairing(tool_name="create_branch", category="pivot"))
    store.reject("create_branch")
    assert store.list_approvals() == []


def test_to_compensation_registry_builds_working_registry(store):
    store.approve(ApprovedPairing(tool_name="create_branch", category="compensable", compensating_tool="delete_branch"))
    store.approve(ApprovedPairing(tool_name="send_email", category="pivot"))

    registry = store.to_compensation_registry()
    assert registry.get("create_branch").category == ActionCategory.COMPENSABLE
    assert registry.get("send_email").category == ActionCategory.PIVOT


def test_persists_across_separate_store_instances(store):
    """Proves this is real disk persistence, not in-memory state."""
    store.approve(ApprovedPairing(tool_name="create_branch", category="pivot"))

    reopened = RegistryStore(path=store._path)
    approvals = reopened.list_approvals()
    assert len(approvals) == 1
    assert approvals[0].tool_name == "create_branch"


def test_approval_persists_server_and_compensation_mapping(store):
    store.approve(
        ApprovedPairing(
            tool_name="seed_database",
            category="compensable",
            compensating_tool="wipe_database",
            server_command="python",
            server_args=["-m", "src.mcp_servers.db_server", "demo.sqlite3"],
            compensation_arg_mapping={},
            tool_description="Insert database records.",
            input_schema={"type": "object", "properties": {"rows": {"type": "array"}}},
        )
    )

    pairing = store.list_approvals()[0]
    assert pairing.server_command == "python"
    assert pairing.server_args[-1] == "demo.sqlite3"
    assert pairing.compensation_arg_mapping == {}
    assert pairing.tool_description == "Insert database records."
    assert pairing.input_schema["properties"]["rows"]["type"] == "array"
    mapper = store.to_compensation_registry().get("seed_database").compensation_arg_mapper
    assert mapper is not None
    assert mapper({"rows": []}) == {}


def test_migrates_existing_registry_schema(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE approved_registry ("
            "tool_name TEXT PRIMARY KEY, category TEXT NOT NULL, compensating_tool TEXT)"
        )
        connection.execute(
            "INSERT INTO approved_registry VALUES (?, ?, ?)",
            ("create_branch", "compensable", "delete_branch"),
        )

    pairing = RegistryStore(database).list_approvals()[0]
    assert pairing.tool_name == "create_branch"
    assert pairing.server_command is None
    assert pairing.server_args == []
