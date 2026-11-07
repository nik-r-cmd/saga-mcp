from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import main
from src.auth.dependencies import get_current_user
from src.auth.supabase_verifier import SupabaseUser
from src.discovery.registry_store import RegistryStore


def test_registry_routes_isolate_approvals_between_users(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_store", RegistryStore(tmp_path / "registry.sqlite3"))
    current_user = {"value": SupabaseUser(user_id="alice", email=None)}
    main.app.dependency_overrides[get_current_user] = lambda: current_user["value"]

    try:
        with TestClient(main.app) as client:
            response = client.post(
                "/registry/approve",
                json={
                    "tool_name": "create_branch",
                    "category": "pivot",
                },
            )
            assert response.status_code == 200
            assert len(client.get("/registry").json()["approved"]) == 1

            current_user["value"] = SupabaseUser(user_id="bob", email=None)
            assert client.get("/registry").json()["approved"] == []
            assert client.delete("/registry/create_branch").status_code == 200

            current_user["value"] = SupabaseUser(user_id="alice", email=None)
            approvals = client.get("/registry").json()["approved"]
            assert len(approvals) == 1
            assert approvals[0]["tool_name"] == "create_branch"
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
