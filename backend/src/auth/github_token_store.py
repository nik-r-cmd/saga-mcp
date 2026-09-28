"""
Stores GitHub access tokens encrypted at rest, never in plaintext.
Tokens are keyed by Supabase user_id (the 'sub' claim from their JWT).

Requires GITHUB_TOKEN_ENCRYPTION_KEY - a Fernet key. Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Set the output as an environment variable before starting the backend.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_STORE_PATH = Path("logs") / "github_tokens.sqlite3"


class TokenEncryptionConfigError(Exception):
    pass


class GitHubTokenStore:
    def __init__(self, path: Path | str = DEFAULT_STORE_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS github_tokens (
                user_id TEXT PRIMARY KEY,
                encrypted_token BLOB NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _fernet() -> Fernet:
        key = os.environ.get("GITHUB_TOKEN_ENCRYPTION_KEY")
        if not key:
            raise TokenEncryptionConfigError(
                "GITHUB_TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\" and set it as an "
                "environment variable."
            )
        return Fernet(key.encode())

    def store_token(self, user_id: str, token: str) -> None:
        encrypted = self._fernet().encrypt(token.encode())
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO github_tokens (user_id, encrypted_token) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET encrypted_token = excluded.encrypted_token
            """,
            (user_id, encrypted),
        )
        conn.commit()
        conn.close()

    def get_token(self, user_id: str) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT encrypted_token FROM github_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        try:
            return self._fernet().decrypt(row[0]).decode()
        except InvalidToken as exc:
            raise TokenEncryptionConfigError(
                "Could not decrypt stored GitHub token - "
                "GITHUB_TOKEN_ENCRYPTION_KEY may have changed since it was stored."
            ) from exc
