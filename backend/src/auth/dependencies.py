"""
FastAPI dependency: get_current_user. Add `user: SupabaseUser =
Depends(get_current_user)` to any route that must be behind the auth
wall. Verifies a Supabase-issued JWT (sent by your Lovable frontend
after login) - the backend itself never handles login/passwords/OAuth,
Supabase does that. A request with no/invalid/expired token gets a real
401.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from src.auth.supabase_verifier import (
    InvalidSupabaseTokenError,
    SupabaseAuthConfigError,
    SupabaseUser,
    verify_supabase_token,
)


def get_current_user(authorization: str | None = Header(default=None)) -> SupabaseUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Bearer <supabase_jwt>",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return verify_supabase_token(token)
    except SupabaseAuthConfigError as exc:
        # A setup problem on the SERVER's side, not the caller's fault -
        # distinguished with a 500, not a 401, so it's obvious in logs
        # that this needs an env var fixed, not a login retry.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except InvalidSupabaseTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
