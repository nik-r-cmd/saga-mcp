from __future__ import annotations

import pytest
from fastapi import HTTPException

import src.api.main as api
from src.api.task_store import TaskResultStore
from src.auth.supabase_verifier import SupabaseUser


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    store = TaskResultStore(tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(api, "_task_results", store)
    return store


def test_task_result_is_stored_and_pollable_only_by_owning_user(task_store):
    request_id = "request-123"
    task_store.create(request_id, "owner")
    message = {
        "type": "task_result",
        "request_id": request_id,
        "status": "completed",
        "saga_id": "saga-123",
    }

    api._record_agent_task_result("other-user", message)
    assert task_store.get(request_id, "owner")["status"] == "running"

    api._record_agent_task_result("owner", message)
    result = api.get_task_result(request_id, SupabaseUser(user_id="owner", email=None))

    assert result == {"status": "completed", "result": message}
    with pytest.raises(HTTPException) as error:
        api.get_task_result(request_id, SupabaseUser(user_id="other-user", email=None))
    assert error.value.status_code == 404


def test_run_task_returns_request_id_used_by_result_polling(task_store, monkeypatch):
    sent = []
    monkeypatch.setattr(api.agents, "is_connected", lambda _: True)
    monkeypatch.setattr(api.agents, "send", lambda user_id, message: sent.append((user_id, message)))
    user = SupabaseUser(user_id="owner", email=None)

    response = api.run_task_via_agent(
        api.RunTaskRequest(
            task_description="test task",
            mcp_command="python",
            mcp_args=[],
        ),
        user,
    )

    request_id = response["request_id"]
    assert response["status"] == "dispatched"
    assert sent[0][1]["request_id"] == request_id
    assert api.get_task_result(request_id, user) == {"status": "running", "result": None}


def test_task_result_survives_store_recreation_and_running_tasks_fail_on_restart(tmp_path):
    database_path = tmp_path / "tasks.sqlite3"
    original = TaskResultStore(database_path)
    original.create("running-task", "owner")
    original.create("completed-task", "owner")
    original.complete("completed-task", "owner", {"status": "completed"})

    restarted = TaskResultStore(database_path)
    restarted.fail_running()

    assert restarted.get("running-task", "owner") == {"status": "failed", "result": None}
    assert restarted.get("completed-task", "owner") == {
        "status": "completed",
        "result": {"status": "completed"},
    }
