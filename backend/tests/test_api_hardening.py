from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.api.main as api
from src.api.task_store import TaskResultStore
from src.auth.supabase_verifier import SupabaseUser


def test_websocket_tickets_are_short_lived_single_use_and_user_bound():
    tickets = api.OneTimeUserTokens()
    ticket = tickets.issue("alice")

    assert tickets.consume(ticket, "bob") is None
    assert tickets.consume(ticket, "alice") is None

    expired = tickets.issue("alice", lifetime_seconds=-1)
    assert tickets.consume(expired, "alice") is None


def test_github_oauth_authorization_creates_user_bound_state(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI",
        "https://dashboard.example.com/auth/github/callback",
    )
    monkeypatch.setattr(api, "_github_oauth_states", api.OneTimeUserTokens())
    user = SupabaseUser(user_id="alice", email=None)

    result = api.github_authorize(user)
    query = parse_qs(urlparse(result["authorization_url"]).query)
    state = query["state"][0]

    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [
        "https://dashboard.example.com/auth/github/callback"
    ]
    tokens = []

    class TokenStore:
        def store_token(self, user_id, token):
            tokens.append((user_id, token))

    monkeypatch.setattr(api, "_github_tokens", TokenStore())
    monkeypatch.setattr(api, "exchange_code_for_token", lambda code, redirect_uri=None: "encrypted-token")
    assert api.github_connect(
        api.GitHubConnectRequest(code="oauth-code", state=state),
        SupabaseUser(user_id="alice", email=None),
    ) == {"status": "connected"}
    assert tokens == [("alice", "encrypted-token")]
    with pytest.raises(HTTPException) as error:
        api.github_connect(
            api.GitHubConnectRequest(code="oauth-code", state=state),
            SupabaseUser(user_id="alice", email=None),
        )
    assert error.value.status_code == 400


def test_github_connection_status_is_user_scoped(monkeypatch):
    class TokenStore:
        def get_token(self, user_id):
            return "access-token" if user_id == "alice" else None

    monkeypatch.setattr(api, "_github_tokens", TokenStore())

    assert api.github_status(SupabaseUser(user_id="alice", email=None)) == {
        "connected": True
    }
    assert api.github_status(SupabaseUser(user_id="bob", email=None)) == {
        "connected": False
    }


def test_backend_mcp_execution_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SAGA_ENABLE_BACKEND_MCP_EXECUTION", raising=False)

    with pytest.raises(HTTPException) as error:
        api.run_task_local(
            api.RunTaskRequest(
                task_description="test",
                mcp_command="python",
                mcp_args=[],
            ),
            SupabaseUser(user_id="alice", email=None),
        )

    assert error.value.status_code == 404


def test_manual_discovery_is_dispatched_to_local_agent(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(api, "_task_results", TaskResultStore(tmp_path / "tasks.sqlite3"))
    monkeypatch.setattr(api.agents, "is_connected", lambda _: True)
    monkeypatch.setattr(
        api.agents,
        "send",
        lambda user_id, message: sent.append((user_id, message)),
    )
    user = SupabaseUser(user_id="alice", email=None)

    response = api.discover_tools_endpoint(
        api.DiscoverRequest(command="python", args=["-m", "example.server"]),
        user,
    )

    assert response["status"] == "dispatched"
    assert sent[0][0] == "alice"
    assert sent[0][1]["type"] == "scan_local_configs"
    assert sent[0][1]["servers"][0]["command"] == "python"
    assert api.get_task_result(response["request_id"], user)["status"] == "running"


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:8080", "http://127.0.0.1:4173"],
)
def test_cors_preflight_allows_local_frontend_dev_origins(origin):
    with TestClient(api.app) as client:
        response = client.options(
            "/agent/status",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_preflight_rejects_unconfigured_external_origin():
    with TestClient(api.app) as client:
        response = client.options(
            "/agent/status",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 400
