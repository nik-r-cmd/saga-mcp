from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from cryptography.fernet import Fernet

from src.auth.github_token_store import GitHubTokenStore, TokenEncryptionConfigError

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN_ENCRYPTION_KEY", TEST_KEY)
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.remove(path)
    s = GitHubTokenStore(path=path)
    yield s
    if os.path.exists(path):
        os.remove(path)


def test_store_and_retrieve_token(store):
    store.store_token("user-1", "ghp_realtoken123")
    assert store.get_token("user-1") == "ghp_realtoken123"


def test_token_is_encrypted_at_rest(store):
    store.store_token("user-2", "ghp_supersecrettoken")
    conn = sqlite3.connect(store._path)
    raw = conn.execute("SELECT encrypted_token FROM github_tokens WHERE user_id = 'user-2'").fetchone()[0]
    conn.close()
    assert b"ghp_supersecrettoken" not in raw


def test_unknown_user_returns_none(store):
    assert store.get_token("nobody") is None


def test_re_storing_updates_token(store):
    store.store_token("user-3", "old-token")
    store.store_token("user-3", "new-token")
    assert store.get_token("user-3") == "new-token"


def test_missing_encryption_key_raises_config_error(monkeypatch, store):
    monkeypatch.delenv("GITHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(TokenEncryptionConfigError):
        store.store_token("user-4", "some-token")
