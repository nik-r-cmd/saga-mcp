from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from src.auth.supabase_verifier import (
    InvalidSupabaseTokenError,
    SupabaseAuthConfigError,
    verify_supabase_token,
)

TEST_SECRET = "test-secret-matching-what-supabase-would-issue"


def _make_token(secret: str = TEST_SECRET, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-abc-123",
        "email": "reviewer@example.com",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def test_valid_token_verifies_correctly(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    token = _make_token()
    user = verify_supabase_token(token)
    assert user.user_id == "user-abc-123"
    assert user.email == "reviewer@example.com"


def test_es256_token_verifies_with_project_jwks(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-abc-123",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    with patch(
        "src.auth.supabase_verifier._get_jwks_client",
        return_value=SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public_key)
        ),
    ):
        user = verify_supabase_token(token)

    assert user.user_id == "user-abc-123"


def test_token_signed_with_wrong_secret_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    token = _make_token(secret="a-completely-different-secret")
    with pytest.raises(InvalidSupabaseTokenError):
        verify_supabase_token(token)


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    now = int(time.time())
    token = _make_token(iat=now - 7200, exp=now - 3600)  # expired an hour ago
    with pytest.raises(InvalidSupabaseTokenError):
        verify_supabase_token(token)


def test_wrong_audience_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    token = _make_token(aud="some-other-app")
    with pytest.raises(InvalidSupabaseTokenError):
        verify_supabase_token(token)


def test_missing_secret_env_var_raises_config_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    token = _make_token()
    with pytest.raises(SupabaseAuthConfigError):
        verify_supabase_token(token)


def test_token_missing_sub_claim_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    now = int(time.time())
    token = jwt.encode(
        {"aud": "authenticated", "iat": now, "exp": now + 3600},  # no "sub"
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidSupabaseTokenError):
        verify_supabase_token(token)
